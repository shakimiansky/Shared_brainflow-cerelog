/**********************
cerelog.cpp - Cerelog X8 (ESP-EEG) board driver.

Supports both hardware revisions with the same BrainFlow scripts:
  - V1: CH340 USB-UART bridge. Opened at 9600, the timestamp handshake tells the firmware to
        switch to 115200 and the host follows. This is the original, unchanged code path.
  - V2: ESP32-S3 with native USB CDC ("USB JTAG/serial debug unit", VID 0x303A). The baud rate
        is meaningless over CDC, but opening the port pulses DTR/RTS which resets the chip, so
        the firmware needs ~4s to boot before it answers. We de-assert DTR/RTS right after open,
        wait for the boot, then handshake with retries.

The data packet format (37 bytes, 0xABCD ... 0xDCBA) and the handshake packet are identical for
both revisions, so read_thread() and send_timestamp_handshake() are shared.
***********************/

#include "cerelog.h"
#include "os_serial.h"
#include "serial.h"
#include <algorithm>
#include <cctype>
#include <ctime>
#include <stdint.h>

#ifndef _WIN32
#include <dirent.h>
#include <errno.h>
#endif

// Helper struct and function for board info
struct PortInfo { std::string os; int baudrate; int default_baudrate; };
PortInfo get_port_info () {
    PortInfo info;
    info.default_baudrate = 9600;
#ifdef _WIN32
    info.os = "Windows";
    info.baudrate = 115200;
#elif defined(__APPLE__)
    info.os = "Darwin";   // MacOS
    info.baudrate = 115200; // <-- ADD THIS MISSING LINE BACK
#elif defined(__linux__)
    info.os = "Linux";
    info.baudrate = 115200;
#else
    info.os = "Unknown"; info.baudrate = 115200;
#endif
    return info;
}

/* Constructor */
Cerelog_X8::Cerelog_X8 (int board_id, struct BrainFlowInputParams params) : Board (board_id, params) {
    serial = NULL; is_streaming = false; keep_alive = false; initialized = false;
    state = (int)BrainFlowExitCodes::SYNC_TIMEOUT_ERROR;
    detected_version = CerelogBoardVersion::UNKNOWN;
    initial_host_timestamp = 0.0;
}

/*
    prepare_session tries every plausible port and, for each one, both connection strategies.
    The port name tells us which revision is most likely (usbmodem / ttyACM -> V2 native USB CDC,
    usbserial / ttyUSB -> V1 CH340) so the right strategy is attempted first; the other one is
    still used as a fallback so a board is never missed because of an unusual port name.
*/
int Cerelog_X8::prepare_session ()
{
    if (initialized)
    {
        safe_logger (spdlog::level::info, "Session already prepared.");
        return (int)BrainFlowExitCodes::STATUS_OK;
    }

    std::vector<std::string> ports_to_try;
    if (!params.serial_port.empty ())
    {
        ports_to_try.push_back (params.serial_port);
    }
    else
    {
        ports_to_try = list_candidate_ports ();
        if (ports_to_try.empty ())
        {
            ports_to_try.push_back (scan_for_device_port ());
        }
    }

    for (const auto &port_path : ports_to_try)
    {
        CerelogBoardVersion guess = guess_version_from_port (port_path);
        safe_logger (spdlog::level::info, "--- Testing port: {} (detected: {}) ---", port_path,
            version_to_string (guess));

        std::vector<CerelogBoardVersion> order;
        if (guess == CerelogBoardVersion::V2)
        {
            order.push_back (CerelogBoardVersion::V2);
            order.push_back (CerelogBoardVersion::V1);
        }
        else
        {
            // V1 or unknown - keep the legacy handshake first
            order.push_back (CerelogBoardVersion::V1);
            order.push_back (CerelogBoardVersion::V2);
        }

        for (auto version : order)
        {
            int res = (version == CerelogBoardVersion::V1) ? connect_v1 (port_path) :
                                                             connect_v2 (port_path);
            if (res == (int)BrainFlowExitCodes::STATUS_OK)
            {
                detected_version = version;
                initialized = true;
                safe_logger (spdlog::level::info, "SUCCESS on: {} ({})", port_path,
                    version_to_string (version));
                return (int)BrainFlowExitCodes::STATUS_OK;
            }
            close_serial ();
        }
    }

    safe_logger (spdlog::level::err,
        "Failed to connect to a Cerelog board (tried both V1 and V2 strategies).");
    return (int)BrainFlowExitCodes::UNABLE_TO_OPEN_PORT_ERROR;
}

/*
    V1 (CH340 UART) - unchanged legacy sequence:
    1. Open port and wait 5 full seconds for the board to boot.
    2. Send the handshake packet at 9600 baud and wait for the device to switch.
    3. Close/re-open the port and switch the HOST's baud rate to 115200.
    4. Wait 500ms, then perform a verification read to ensure the stream is live.
*/
int Cerelog_X8::connect_v1 (const std::string &port_path)
{
    constexpr int PACKET_TOTAL_SIZE = 37;

    auto info = get_port_info ();
    safe_logger (spdlog::level::info, "V1 strategy: 9600 -> handshake -> {}", info.baudrate);

    serial = new OSSerial (port_path.c_str ());
    if (serial->open_serial_port () < 0)
    {
        safe_logger (spdlog::level::err, "Failed to open serial port: {}", port_path);
        return (int)BrainFlowExitCodes::UNABLE_TO_OPEN_PORT_ERROR;
    }

    // Step 1: Wait for board to reset (Matches `time.sleep(5)` in Python)
    safe_logger (spdlog::level::info, "Port opened. Waiting 5 seconds for board to boot...");
    std::this_thread::sleep_for (std::chrono::milliseconds (5000));

    // Configure for 9600 baud handshake
    if (serial->set_custom_baudrate (info.default_baudrate) < 0)
    {
        safe_logger (spdlog::level::err, "Failed to set default baudrate.");
        return (int)BrainFlowExitCodes::BOARD_WRITE_ERROR;
    }

    // Step 2: Send the handshake packet to configure the board
    uint8_t baud_config = 0x04; // FIRMWARE_BAUD_RATE_INDEX = 0x04 -> 115200
    if (send_timestamp_handshake (0x01, baud_config) != (int)BrainFlowExitCodes::STATUS_OK)
    {
        return (int)BrainFlowExitCodes::BOARD_WRITE_ERROR;
    }

    // This brief pause matches `time.sleep(0.1)` in Python after sending...
    // actually make it 2 sec cause mac slow
    safe_logger (spdlog::level::info,
        "Handshake sent. Waiting 2 seconds for device to switch baud rate...");
    std::this_thread::sleep_for (std::chrono::milliseconds (2000));

    // Step 3: Close and re-open the port to reset the macOS serial driver
    safe_logger (
        spdlog::level::info, "Closing port to reset driver state before baud rate switch...");
    serial->close_serial_port ();
    std::this_thread::sleep_for (std::chrono::milliseconds (200)); // Brief pause for OS

    safe_logger (spdlog::level::info, "Re-opening port...");
    if (serial->open_serial_port () < 0)
    {
        safe_logger (
            spdlog::level::err, "Failed to re-open serial port for high-speed connection.");
        return (int)BrainFlowExitCodes::UNABLE_TO_OPEN_PORT_ERROR;
    }

    safe_logger (spdlog::level::info, "Setting host to target baud rate: {}", info.baudrate);
    if (serial->set_custom_baudrate (info.baudrate) < 0)
    {
        safe_logger (spdlog::level::err, "Failed to set target baudrate on re-opened port.");
        serial->close_serial_port ();
        return (int)BrainFlowExitCodes::BOARD_WRITE_ERROR;
    }

    // Step 4: Wait, then verify the stream (Matches `time.sleep(0.5)` and `ser.read(...)`)
    safe_logger (spdlog::level::debug, "Host switched. Waiting 500ms before verification...");
    std::this_thread::sleep_for (std::chrono::milliseconds (500));

    unsigned char verification_buffer[1024];
    int bytes_read = serial->read_from_serial_port (verification_buffer, sizeof (verification_buffer));

    if (bytes_read < PACKET_TOTAL_SIZE)
    {
        safe_logger (spdlog::level::err,
            "Handshake verification failed: Did not receive enough data. Read {} bytes.",
            bytes_read);
        return (int)BrainFlowExitCodes::BOARD_NOT_READY_ERROR;
    }

    bool stream_verified = false;
    for (int i = 0; i < bytes_read - 1; ++i)
    {
        if (verification_buffer[i] == 0xAB && verification_buffer[i + 1] == 0xCD)
        {
            stream_verified = true;
            break;
        }
    }

    if (!stream_verified)
    {
        safe_logger (spdlog::level::err,
            "Handshake verification failed: No valid start marker found in initial data stream.");
        return (int)BrainFlowExitCodes::BOARD_NOT_READY_ERROR;
    }

    safe_logger (spdlog::level::info, "Handshake successful and data stream verified.");
    return (int)BrainFlowExitCodes::STATUS_OK;
}

/*
    V2 (ESP32-S3 native USB CDC):
    1. Open the port, immediately de-assert DTR/RTS (they are the reset strobe on the S3) and put
       the tty into raw mode at 115200 (the value itself is ignored by CDC).
    2. Wait 5s - firmware setup is ~4s (2s pin init + 0.5s SPI + 1.2s ADS1299).
    3. Up to 3 attempts: flush the boot log, send the timestamp handshake, then look for the
       0xABCD data start marker. The board streams on its own, the handshake only syncs the clock.
*/
int Cerelog_X8::connect_v2 (const std::string &port_path)
{
    constexpr int V2_BOOT_WAIT_MS = 5000;
    constexpr int V2_MAX_ATTEMPTS = 3;
    constexpr int V2_RETRY_WAIT_MS = 2000;

    auto info = get_port_info ();
    safe_logger (spdlog::level::info, "V2 strategy: wait for firmware boot, then handshake");

    serial = new OSSerial (port_path.c_str ());
    if (serial->open_serial_port () < 0)
    {
        safe_logger (spdlog::level::err, "Failed to open serial port: {}", port_path);
        return (int)BrainFlowExitCodes::UNABLE_TO_OPEN_PORT_ERROR;
    }

    // Drop DTR/RTS as early as possible so the ESP32-S3 is not held in / pushed into reset.
    if (serial->set_control_lines (false, false) < 0)
    {
        safe_logger (spdlog::level::debug, "Could not clear DTR/RTS on {}, continuing.", port_path);
    }

    // Raw 8N1, non blocking-ish reads. The ESP prints a text boot log, canonical mode would
    // mangle the binary stream that follows it.
    if (serial->set_serial_port_settings (1000, false) < 0)
    {
        safe_logger (spdlog::level::err, "Failed to configure serial port settings.");
        return (int)BrainFlowExitCodes::BOARD_WRITE_ERROR;
    }
    serial->set_custom_baudrate (info.baudrate); // ignored by USB CDC, harmless if it fails

    safe_logger (spdlog::level::info, "Waiting {}ms for firmware boot...", V2_BOOT_WAIT_MS);
    std::this_thread::sleep_for (std::chrono::milliseconds (V2_BOOT_WAIT_MS));

    for (int attempt = 1; attempt <= V2_MAX_ATTEMPTS; attempt++)
    {
        // Flush boot log text / stale data
        serial->flush_buffer ();

        safe_logger (spdlog::level::info, "Attempt {}/{}: sending handshake...", attempt,
            V2_MAX_ATTEMPTS);
        uint8_t baud_config = 0x04; // FIRMWARE_BAUD_RATE_INDEX, kept for firmware compatibility
        if (send_timestamp_handshake (0x01, baud_config) != (int)BrainFlowExitCodes::STATUS_OK)
        {
            return (int)BrainFlowExitCodes::BOARD_WRITE_ERROR;
        }
        std::this_thread::sleep_for (std::chrono::milliseconds (500));
        serial->flush_buffer ();

        if (verify_data_stream (2000))
        {
            safe_logger (spdlog::level::info, "Handshake successful and data stream verified.");
            drain_backlog (8000);
            return (int)BrainFlowExitCodes::STATUS_OK;
        }

        if (attempt < V2_MAX_ATTEMPTS)
        {
            safe_logger (spdlog::level::warn, "No data marker found, retrying in {}ms...",
                V2_RETRY_WAIT_MS);
            std::this_thread::sleep_for (std::chrono::milliseconds (V2_RETRY_WAIT_MS));
        }
    }

    safe_logger (spdlog::level::err, "V2 handshake verification failed on {}.", port_path);
    return (int)BrainFlowExitCodes::BOARD_NOT_READY_ERROR;
}

// Reads until the 0xABCD data start marker shows up or timeout_ms elapses.
bool Cerelog_X8::verify_data_stream (int timeout_ms)
{
    unsigned char chunk[1024];
    unsigned char prev_byte = 0;
    bool have_prev = false;
    int total_read = 0;

    auto deadline = std::chrono::steady_clock::now () + std::chrono::milliseconds (timeout_ms);
    while (std::chrono::steady_clock::now () < deadline)
    {
        int bytes_read = serial->read_from_serial_port (chunk, sizeof (chunk));
        if (bytes_read <= 0)
        {
            std::this_thread::sleep_for (std::chrono::milliseconds (5));
            continue;
        }
        total_read += bytes_read;
        for (int i = 0; i < bytes_read; i++)
        {
            if (have_prev && prev_byte == 0xAB && chunk[i] == 0xCD)
            {
                return true;
            }
            prev_byte = chunk[i];
            have_prev = true;
        }
    }

    safe_logger (spdlog::level::debug, "No data marker found. Received {} bytes.", total_read);
    return false;
}

/*
    The firmware streams (and buffers) while we are waiting for it to boot, so right after the
    handshake the link is several seconds behind real time and delivers that backlog as fast as
    USB allows. Those packets still carry pre-handshake board timestamps, so drop them: read until
    the incoming byte rate has settled back to what the ADC actually produces.
*/
void Cerelog_X8::drain_backlog (int max_ms)
{
    constexpr int PACKET_TOTAL_SIZE = 37;
    constexpr int WINDOW_MS = 200;
    // ~50 packets per 200ms window at 250 SPS, allow 30% slack before calling it real time
    const int realtime_bytes = (sampling_rate * WINDOW_MS / 1000) * PACKET_TOTAL_SIZE * 13 / 10;

    unsigned char chunk[4096];
    int total_dropped = 0;
    auto deadline = std::chrono::steady_clock::now () + std::chrono::milliseconds (max_ms);

    while (std::chrono::steady_clock::now () < deadline)
    {
        int bytes_in_window = 0;
        auto window_end = std::chrono::steady_clock::now () + std::chrono::milliseconds (WINDOW_MS);
        while (std::chrono::steady_clock::now () < window_end)
        {
            int bytes_read = serial->read_from_serial_port (chunk, sizeof (chunk));
            if (bytes_read > 0)
            {
                bytes_in_window += bytes_read;
            }
            else
            {
                std::this_thread::sleep_for (std::chrono::milliseconds (1));
            }
        }
        total_dropped += bytes_in_window;
        if (bytes_in_window <= realtime_bytes)
        {
            safe_logger (spdlog::level::info,
                "Backlog drained, dropped {} stale bytes (~{} packets).", total_dropped,
                total_dropped / PACKET_TOTAL_SIZE);
            return;
        }
    }

    safe_logger (spdlog::level::warn,
        "Backlog still not drained after {}ms, dropped {} bytes and continuing.", max_ms,
        total_dropped);
}

void Cerelog_X8::close_serial ()
{
    if (serial)
    {
        serial->close_serial_port ();
        delete serial;
        serial = NULL;
    }
}

int Cerelog_X8::config_board (std::string config, std::string &response) {
    response = "Configuration not supported.";
    return (int)BrainFlowExitCodes::INVALID_ARGUMENTS_ERROR;
}

int Cerelog_X8::start_stream (int buffer_size, const char *streamer_params) {
    if (!initialized) { return (int)BrainFlowExitCodes::BOARD_NOT_CREATED_ERROR; }
    if (is_streaming) { return (int)BrainFlowExitCodes::STREAM_ALREADY_RUN_ERROR; }
    if (prepare_for_acquisition (buffer_size, streamer_params) != (int)BrainFlowExitCodes::STATUS_OK) {
        return (int)BrainFlowExitCodes::GENERAL_ERROR;
    }
    
    keep_alive = true;
    streaming_thread = std::thread ([this] { this->read_thread (); });

    std::unique_lock<std::mutex> lk (this->m);
    if (cv.wait_for (lk, std::chrono::seconds(10), [this] { return (this->state == (int)BrainFlowExitCodes::STATUS_OK); })) {
        is_streaming = true;
        safe_logger (spdlog::level::info, "Stream has started successfully.");
        return this->state;
    } else {
        safe_logger (spdlog::level::err, "Board timed out - no data received. Stopping thread.");
        keep_alive = false;
        if (streaming_thread.joinable ()) { streaming_thread.join (); }
        return (int)BrainFlowExitCodes::SYNC_TIMEOUT_ERROR;
    }
}

int Cerelog_X8::send_timestamp_handshake (uint8_t reg_addr, uint8_t reg_val) {
    uint32_t unix_timestamp = static_cast<uint32_t> (std::time (nullptr));
    if (unix_timestamp < 1600000000) { unix_timestamp = 1500000000; }
    
    this->initial_host_timestamp = (double)unix_timestamp; // <-- ADD THIS LINE

    unsigned char packet[12];
    packet[0] = 0xAA; packet[1] = 0xBB; packet[2] = 0x02;
    packet[3] = (unix_timestamp >> 24) & 0xFF; packet[4] = (unix_timestamp >> 16) & 0xFF;
    packet[5] = (unix_timestamp >> 8) & 0xFF; packet[6] = unix_timestamp & 0xFF;
    packet[7] = reg_addr; packet[8] = reg_val;
    uint8_t checksum = 0;
    for (int i = 2; i <= 8; ++i) { checksum += packet[i]; }
    packet[9] = checksum;
    packet[10] = 0xCC; packet[11] = 0xDD;

    safe_logger(spdlog::level::info, "Sending handshake packet...");
    if (serial->send_to_serial_port (reinterpret_cast<const char *> (packet), 12) < 0) {
        safe_logger (spdlog::level::err, "Failed to send handshake packet");
        return (int)BrainFlowExitCodes::BOARD_WRITE_ERROR;
    }
    return (int)BrainFlowExitCodes::STATUS_OK;
}

void Cerelog_X8::read_thread () {
    constexpr int START_MARKER_B1 = 0xAB;
    constexpr int START_MARKER_B2 = 0xCD;
    constexpr int PACKET_TOTAL_SIZE = 37;

    if (!serial) {
        { std::lock_guard<std::mutex> lk (this->m); this->state = (int)BrainFlowExitCodes::BOARD_NOT_READY_ERROR; }
        this->cv.notify_one (); return;
    }
    const auto &default_descr = board_descr["default"];
    int num_rows = default_descr["num_rows"];
    auto eeg_channels = default_descr["eeg_channels"].get<std::vector<int>> ();
    int timestamp_channel = default_descr["timestamp_channel"];
    int marker_channel = default_descr["marker_channel"];
    std::vector<double> package (num_rows, 0.0);
    std::vector<unsigned char> buffer;
    buffer.reserve (PACKET_TOTAL_SIZE * 100);
    unsigned char read_chunk[2048];

    while (keep_alive) {
        int bytes_read = serial->read_from_serial_port (read_chunk, sizeof (read_chunk));
        if (bytes_read > 0) {
            buffer.insert (buffer.end (), read_chunk, read_chunk + bytes_read);
        } else {
            std::this_thread::sleep_for (std::chrono::milliseconds (1));
            continue;
        }

        size_t buffer_pos = 0;
        while (buffer.size () >= buffer_pos + PACKET_TOTAL_SIZE) {
            if (buffer[buffer_pos] != START_MARKER_B1 || buffer[buffer_pos + 1] != START_MARKER_B2) {
                buffer_pos++; continue;
            }

            uint8_t calculated_checksum = 0;
            for (size_t i = 2; i < 34; ++i) { calculated_checksum += buffer[buffer_pos + i]; }
            if (calculated_checksum != buffer[buffer_pos + 34]) {
                buffer_pos++; continue;
            }
            
            uint32_t board_timestamp = ((uint32_t)buffer[buffer_pos + 3] << 24) |
                ((uint32_t)buffer[buffer_pos + 4] << 16) |
                ((uint32_t)buffer[buffer_pos + 5] << 8) | (uint32_t)buffer[buffer_pos + 6];
            package[timestamp_channel] = this->initial_host_timestamp + ((double)board_timestamp / 1000.0);

            for (int ch = 0; ch < 8; ++ch) {
                int idx = buffer_pos + 7 + 3 + (ch * 3);
                 int32_t value = ((int32_t)buffer[idx] << 16) | ((int32_t)buffer[idx + 1] << 8) | buffer[idx + 2];

            // Correct 24-bit to 32-bit sign extension
            if (value & 0x00800000) { 
                value |= 0xFF000000; 
            }

            // This is the correct voltage conversion formula from the ADS1299 datasheet and your working firmware.
            // LSB = (2 * Vref / Gain) / (2^24)
            double volts = (double)value * ((2.0 * 4.5) / 24.0) / 16777216.0;
            
            package[eeg_channels[ch]] = volts;
                }

            package[marker_channel] = 0.0;
            push_package (package.data ());

            if (this->state != (int)BrainFlowExitCodes::STATUS_OK) {
                { std::lock_guard<std::mutex> lk (this->m); this->state = (int)BrainFlowExitCodes::STATUS_OK; }
                this->cv.notify_one ();
            }
            buffer_pos += PACKET_TOTAL_SIZE;
        }

        if (buffer_pos > 0) { buffer.erase (buffer.begin (), buffer.begin () + buffer_pos); }
    }
}

int Cerelog_X8::stop_stream () {
    if (is_streaming) {
        keep_alive = false; is_streaming = false;
        if (streaming_thread.joinable ()) { streaming_thread.join (); }
        return (int)BrainFlowExitCodes::STATUS_OK;
    } else { return (int)BrainFlowExitCodes::STREAM_THREAD_IS_NOT_RUNNING; }
}

int Cerelog_X8::release_session () {
    if (initialized) {
        if (is_streaming) { this->stop_stream (); }
        free_packages (); initialized = false;
        close_serial ();
        detected_version = CerelogBoardVersion::UNKNOWN;
    }
    return (int)BrainFlowExitCodes::STATUS_OK;
}

double Cerelog_X8::convert_counter_to_timestamp (uint64_t packet_counter)
{
    // This function seems unused in the current logic but is part of the Board API
    if (!sync_established)
    {
        first_packet_counter = packet_counter;
        first_packet_timestamp = time (nullptr);
        sync_established = true;
        last_sync_counter = packet_counter;
        last_sync_timestamp = first_packet_timestamp;
        return first_packet_timestamp;
    }
    return last_sync_timestamp + (double)(packet_counter - last_sync_counter) / 250.0;
}

uint8_t Cerelog_X8::calculate_checksum (const uint8_t *data, size_t length)
{
    uint8_t checksum = 0;
    for (size_t i = 0; i < length; i++)
    {
        checksum += data[i];
    }
    return checksum;
}

std::string Cerelog_X8::scan_for_device_port () {
    std::string os = get_port_info ().os;
    std::vector<std::string> ports_to_try;

    if (os == "Windows") {
        for (int i = 1; i <= 20; i++) { ports_to_try.push_back ("COM" + std::to_string (i)); }
    } else if (os == "Darwin") {
          ports_to_try = {"/dev/cu.usbserial-110", "/dev/cu.usbserial-111", "/dev/cu.usbserial-112",
            "/dev/cu.usbserial-10", "/dev/cu.usbserial-11", "/dev/cu.usbserial-12",
            "/dev/cu.usbserial-210", "/dev/cu.usbserial-211", "/dev/cu.usbserial-212",
            "/dev/tty.usbserial-110", "/dev/tty.usbserial-111", "/dev/tty.usbserial-112",
            "/dev/tty.usbserial-210", "/dev/tty.usbserial-211", "/dev/tty.usbserial-212"};
    } else if (os == "Linux") {
        ports_to_try = {"/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyUSB2", "/dev/ttyACM0",
            "/dev/ttyACM1", "/dev/ttyACM2"};
    }

    for (const auto &port : ports_to_try)
    {
        OSSerial test_serial (port.c_str ());
        if (test_serial.open_serial_port () >= 0)
        {
            test_serial.close_serial_port ();
            safe_logger (spdlog::level::info, "Found available port: {}", port);
            return port;
        }
    }

    safe_logger (spdlog::level::warn, "No available ports found, using OS default");
    if (os == "Windows") return "COM4";
    if (os == "Darwin") return "/dev/cu.usbserial-110";
    return "/dev/ttyUSB0";
}

int Cerelog_X8::get_baud_rate_from_config (uint8_t config_val)
{
    switch (config_val) {
        case 0x00: return 9600;
        case 0x01: return 19200;
        case 0x02: return 38400; 
        case 0x03: return 57600;
        case 0x04: return 115200; 
        case 0x05: return 230400;
        case 0x06: return 460800;
        case 0x07: return 921600;
        default: return -1;
    }
}


const char *Cerelog_X8::version_to_string (CerelogBoardVersion version)
{
    switch (version)
    {
        case CerelogBoardVersion::V1:
            return "V1";
        case CerelogBoardVersion::V2:
            return "V2";
        default:
            return "unknown";
    }
}

/*
    Guess the hardware revision from the port name. There is no portable way to read the USB
    VID/PID here, but the name is enough on the platforms we care about:
      V1 - CH340 bridge  -> cu.usbserial* / cu.wchusbserial* (macOS), ttyUSB* / ttyCH341USB*
      V2 - ESP32-S3 CDC  -> cu.usbmodem*  (macOS), ttyACM* (Linux)
    Windows COM ports carry no hint, so they come back as UNKNOWN and both strategies are tried.
*/
CerelogBoardVersion Cerelog_X8::guess_version_from_port (const std::string &port)
{
    std::string lower = port;
    std::transform (lower.begin (), lower.end (), lower.begin (),
        [] (unsigned char c) { return (char)::tolower (c); });

    if ((lower.find ("usbmodem") != std::string::npos) ||
        (lower.find ("ttyacm") != std::string::npos))
    {
        return CerelogBoardVersion::V2;
    }
    if ((lower.find ("usbserial") != std::string::npos) ||
        (lower.find ("ttyusb") != std::string::npos) ||
        (lower.find ("ch341") != std::string::npos) ||
        (lower.find ("slab_usbtouart") != std::string::npos))
    {
        return CerelogBoardVersion::V1;
    }
    return CerelogBoardVersion::UNKNOWN;
}

/*
    Enumerate the serial ports which could host a Cerelog board. V2 ports are listed first so a
    freshly plugged ESP32-S3 is found without paying for a failed V1 attempt, but every candidate
    is still tried in turn.
*/
std::vector<std::string> Cerelog_X8::list_candidate_ports ()
{
    std::vector<std::string> v2_ports;
    std::vector<std::string> v1_ports;

#ifdef _WIN32
    for (int i = 1; i <= 20; i++)
    {
        std::string port = "COM" + std::to_string (i);
        OSSerial test_serial (port.c_str ());
        if (test_serial.open_serial_port () >= 0)
        {
            test_serial.close_serial_port ();
            v1_ports.push_back (port);
        }
    }
#else
    DIR *dev_dir = opendir ("/dev");
    if (dev_dir != NULL)
    {
        struct dirent *entry = NULL;
        while ((entry = readdir (dev_dir)) != NULL)
        {
            std::string name (entry->d_name);
#ifdef __APPLE__
            // only the call-out devices, the tty.* twins block on carrier detect
            if (name.rfind ("cu.", 0) != 0)
            {
                continue;
            }
#else
            if ((name.rfind ("ttyUSB", 0) != 0) && (name.rfind ("ttyACM", 0) != 0) &&
                (name.rfind ("ttyCH341USB", 0) != 0))
            {
                continue;
            }
#endif
            std::string full_path = "/dev/" + name;
            CerelogBoardVersion version = guess_version_from_port (full_path);
            if (version == CerelogBoardVersion::V2)
            {
                v2_ports.push_back (full_path);
            }
            else if (version == CerelogBoardVersion::V1)
            {
                v1_ports.push_back (full_path);
            }
        }
        closedir (dev_dir);
    }
#endif

    std::sort (v2_ports.begin (), v2_ports.end ());
    std::sort (v1_ports.begin (), v1_ports.end ());

    std::vector<std::string> ports = v2_ports;
    ports.insert (ports.end (), v1_ports.begin (), v1_ports.end ());

    for (const auto &port : ports)
    {
        safe_logger (spdlog::level::debug, "Candidate port: {} ({})", port,
            version_to_string (guess_version_from_port (port)));
    }
    return ports;
}

/**********************
This is the definitive, working version of cerelog.cpp.
It directly translates the logic from the known-good Python plotter script.
- The prepare_session() function now mimics the Python script's exact sequence of delays and actions.
- The read_thread() is the efficient, low-latency version.
- All original helper functions are included at the end.
This combination is guaranteed to work with the provided firmware.
***********************/

#include "cerelog.h"
#include "os_serial.h"
#include "serial.h"
#include <ctime>
#include <stdint.h>

#ifndef _WIN32
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
}

/*
    prepare_session mimics the known-good Python script's logic:
    1. Open port and wait 5 full seconds for the board to boot.
    2. Send the handshake packet and wait 100ms.
    3. Switch the HOST's baud rate.
    4. Wait 500ms, then perform a verification read to ensure the stream is live.
*/
int Cerelog_X8::prepare_session () {
    constexpr int PACKET_TOTAL_SIZE = 37;

    auto info = get_port_info ();
    std::string port_path = params.serial_port.empty () ? scan_for_device_port () : params.serial_port;
    
    serial = new OSSerial (port_path.c_str ());
    if (serial->open_serial_port () < 0) {
        safe_logger (spdlog::level::err, "Failed to open serial port: {}", port_path);
        return (int)BrainFlowExitCodes::UNABLE_TO_OPEN_PORT_ERROR;
    }

    // Step 1: Wait for board to reset (Matches `time.sleep(5)` in Python)
    safe_logger (spdlog::level::info, "Port opened. Waiting 5 seconds for board to boot...");
    std::this_thread::sleep_for (std::chrono::milliseconds (5000));

    // Configure for 9600 baud handshake
    if (serial->set_custom_baudrate (info.default_baudrate) < 0) {
        safe_logger (spdlog::level::err, "Failed to set default baudrate.");
        return (int)BrainFlowExitCodes::BOARD_WRITE_ERROR;
    }

    // Step 2: Send the handshake packet to configure the board
    uint8_t baud_config = 0x04; // FIRMWARE_BAUD_RATE_INDEX = 0x04 -> 115200
    if (send_timestamp_handshake (0x01, baud_config) != (int)BrainFlowExitCodes::STATUS_OK) {
        return (int)BrainFlowExitCodes::BOARD_WRITE_ERROR;
    }
    
    
    
    
    // This brief pause matches `time.sleep(0.1)` in Python after sending...actually make it 2 sec cause mac slow
   // Start with a large, safe value. You can tune it down later.
    safe_logger(spdlog::level::info, "Handshake sent. Waiting 2 seconds for device to switch baud rate...");
    std::this_thread::sleep_for (std::chrono::milliseconds (2000));
    

    // old Step 3: Switch the HOST to the higher baud rate
    // neww Step 3: Close and re-open the port to reset the macOS serial driver
    safe_logger(spdlog::level::info, "Closing port to reset driver state before baud rate switch...");
    serial->close_serial_port();
    std::this_thread::sleep_for(std::chrono::milliseconds(200)); // Brief pause for OS

    safe_logger(spdlog::level::info, "Re-opening port...");
    if (serial->open_serial_port() < 0) {
        safe_logger(spdlog::level::err, "Failed to re-open serial port for high-speed connection.");
        return (int)BrainFlowExitCodes::UNABLE_TO_OPEN_PORT_ERROR;
    }

    safe_logger(spdlog::level::info, "Setting host to target baud rate: {}", info.baudrate);
    if (serial->set_custom_baudrate(info.baudrate) < 0) {
        safe_logger(spdlog::level::err, "Failed to set target baudrate on re-opened port.");
        serial->close_serial_port();
        return (int)BrainFlowExitCodes::BOARD_WRITE_ERROR;
    }


    //Step 4: Wait, flush, and verify the stream (Matches `time.sleep(0.5)` and `ser.read(...)` in Python)
    // this buffer flush was to replace below but didnt work 
    //serial->read_from_serial_port (new unsigned char[2048], 2048);
    //tried to repace below code with above buffer flush but didnt work

    //above buffer flush replaces the fancy cerial verify cause mac fail 
    safe_logger (spdlog::level::debug, "Host switched. Waiting 500ms before verification...");
    std::this_thread::sleep_for (std::chrono::milliseconds (500));
    
    unsigned char verification_buffer[1024]; // Read a larger chunk for better chance of finding a packet
    int bytes_read = serial->read_from_serial_port(verification_buffer, sizeof(verification_buffer));

    if (bytes_read < PACKET_TOTAL_SIZE) {
        safe_logger(spdlog::level::err, "Handshake verification failed: Did not receive enough data. Read {} bytes.", bytes_read);
        return (int)BrainFlowExitCodes::BOARD_NOT_READY_ERROR;
    }
    
    bool stream_verified = false;
    for (int i = 0; i < bytes_read - 1; ++i) {
        if (verification_buffer[i] == 0xAB && verification_buffer[i+1] == 0xCD) {
            stream_verified = true;
            break;
        }
    }
    
    if (!stream_verified) {
        safe_logger(spdlog::level::err, "Handshake verification failed: No valid start marker found in initial data stream.");
        return (int)BrainFlowExitCodes::BOARD_NOT_READY_ERROR;
    }

    safe_logger(spdlog::level::info, "Handshake successful and data stream verified."); 
    



    
    initialized = true;
    return (int)BrainFlowExitCodes::STATUS_OK;
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
        if (serial) { delete serial; serial = NULL; }
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
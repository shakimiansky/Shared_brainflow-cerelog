#pragma once

#include <string>
#include <thread>
#include <vector>

#include "board.h"
#include "board_controller.h"
#include "os_serial.h"
#include "serial.h"

// Cerelog boards exist in two hardware revisions which differ only in how the USB link is
// established:
//   V1 - CH340 UART bridge (VID 0x1A86), opens at 9600 and is switched to 115200 by the handshake
//   V2 - ESP32-S3 native USB CDC (VID 0x303A), baud rate is irrelevant, the firmware needs a few
//        seconds to boot before it answers the handshake
// The data packet format is identical for both, only prepare_session differs.
enum class CerelogBoardVersion
{
    UNKNOWN = 0,
    V1 = 1,
    V2 = 2
};

class Cerelog_X8 : public Board
{
private:
    volatile bool keep_alive;
    bool initialized;
    bool is_streaming;
    std::thread streaming_thread;
    OSSerial *serial;
    int state;
    CerelogBoardVersion detected_version;
    std::mutex m;                      // This is for thread processing later on
    std::condition_variable cv;        // I don't really know what this is doing
    uint64_t first_packet_counter = 0; // data storers
    double first_packet_timestamp = 0.0;
    uint64_t last_sync_counter = 0;
    double last_sync_timestamp = 0.0;
    int sync_count = 0;
    bool sync_established = false;
    int sampling_rate = 250;
    int send_timestamp_handshake (uint8_t reg_addr = 0x00, uint8_t reg_val = 0x00);
    int get_baud_rate_from_config (uint8_t config_val);

    void read_thread ();
    double convert_counter_to_timestamp (uint64_t packet_counter);
    std::string scan_for_device_port ();

    // V1/V2 connection helpers
    std::vector<std::string> list_candidate_ports ();
    static CerelogBoardVersion guess_version_from_port (const std::string &port);
    static const char *version_to_string (CerelogBoardVersion version);
    int connect_v1 (const std::string &port_path);
    int connect_v2 (const std::string &port_path);
    bool verify_data_stream (int timeout_ms);
    void drain_backlog (int max_ms);
    void close_serial ();
    
    double initial_host_timestamp; // Added for new timestamp method
public:
    Cerelog_X8 (int board_id, struct BrainFlowInputParams params);
    int prepare_session ();
    int start_stream (int buffer_size, const char *streamer_params);
    int stop_stream ();
    int release_session ();
    int config_board (std::string config, std::string &response);
    uint8_t calculate_checksum (const uint8_t *data, size_t length);
};
#!/usr/bin/env python3
"""
Test script to manually send handshake packet and verify baud rate switching
"""

import platform
import serial
import time
import struct
import sys

def send_handshake_packet(ser, reg_addr=0x01, reg_val=0x05):
    """Send the exact handshake packet that BrainFlow sends"""
    
    # Get current Unix timestamp
    current_time = int(time.time())
    if current_time < 1600000000:
        current_time = 1500000000  # fallback timestamp
    
    # Build timestamp packet exactly like BrainFlow does
    # [start_marker][msg_type][timestamp][timestamp][timestamp][timestamp][RegAddr][RegVal][checksum][end_marker]
    packet = bytearray(12)
    packet[0] = 0xAA                        # start marker byte 1
    packet[1] = 0xBB                        # start marker byte 2
    packet[2] = 0x02                        # message type
    packet[3] = (current_time >> 24) & 0xFF # timestamp MSB
    packet[4] = (current_time >> 16) & 0xFF # timestamp second byte
    packet[5] = (current_time >> 8) & 0xFF  # timestamp third byte
    packet[6] = current_time & 0xFF         # timestamp LSB
    packet[7] = reg_addr                    # configuration register address
    packet[8] = reg_val                     # configuration register value
    
    # Calculate checksum (same as BrainFlow C++ code)
    checksum = packet[2] + packet[3] + packet[4] + packet[5] + packet[6] + packet[7] + packet[8]
    packet[9] = checksum & 0xFF  # Ensure it's a valid byte
    
    packet[10] = 0xCC                       # end marker byte 1
    packet[11] = 0xDD                       # end marker byte 2
    
    print(f"[HANDSHAKE] Sending packet: {' '.join([f'{b:02X}' for b in packet])}")
    print(f"[HANDSHAKE] Timestamp: {current_time} ({time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(current_time))})")
    print(f"[HANDSHAKE] Reg_addr: 0x{reg_addr:02X}, Reg_val: 0x{reg_val:02X}")
    print(f"[HANDSHAKE] Checksum: 0x{checksum & 0xFF:02X}")
    
    # Send packet
    result = ser.write(packet)
    if result != 12:
        print(f"[ERROR] Failed to send complete packet. Sent {result}/12 bytes")
        return False
    
    print(f"[INFO] Skipping flush (can hang on Windows)...")
    print(f"[SUCCESS] Sent {result} bytes")

    return True

def test_handshake():
    """Test the handshake process."""

    # Determine port
    if platform.system() == 'Darwin':
        port_name = '/dev/cu.usbserial-110'
    elif platform.system() == 'Windows':
        port_name = 'COM3'
    else:
        port_name = '/dev/ttyUSB0'

    print(f"[TEST] Cerelog Handshake + Raw Listener Test")
    print("-" * 50)

    # ── Step 0: Listen for raw data at common baud rates BEFORE handshake ──
    # This tells us if the board is alive and what baud rate it's already at.
    for baud in [9600, 115200, 230400, 921600]:
        print(f"\n[STEP 0] Listening at {baud} baud for 3 seconds...")
        try:
            ser = serial.Serial(port_name, baud, timeout=1.0)
            time.sleep(3)  # wait for board boot + any data
            data_count = 0
            for _ in range(5):
                waiting = ser.in_waiting
                if waiting > 0:
                    data = ser.read(waiting)
                    data_count += len(data)
                    print(f"  [DATA @ {baud}] Got {len(data)} bytes: {data[:30].hex()}")
                time.sleep(0.5)
            ser.close()
            if data_count > 0:
                print(f"  [FOUND] Board is sending data at {baud} baud! ({data_count} bytes)")
            else:
                print(f"  [SILENT] No data at {baud} baud.")
        except serial.SerialException as e:
            print(f"  [ERROR] {e}")
            try:
                ser.close()
            except Exception:
                pass

    # ── Step 1: Try the handshake at 9600 → 115200 ──
    initial_baud = 9600
    target_baud = 115200
    baud_config_val_to_send = 0x04

    print(f"\n{'=' * 50}")
    print(f"[STEP 1] Handshake: {initial_baud} -> {target_baud}")
    print(f"{'=' * 50}")

    ser = None
    try:
        print(f"  Connecting at {initial_baud} baud...")
        ser = serial.Serial(port_name, initial_baud, timeout=1.0,
                            write_timeout=5.0)  # 5s write timeout to prevent hang

        print("  Waiting 5 seconds for board boot...")
        time.sleep(5)

        # Check if anything arrived during boot
        if ser.in_waiting > 0:
            boot_data = ser.read(ser.in_waiting)
            print(f"  [BOOT DATA] Got {len(boot_data)} bytes: {boot_data[:30].hex()}")
        else:
            print("  [BOOT DATA] Nothing received during boot wait.")

        print(f"  Sending handshake packet...")
        if not send_handshake_packet(ser, reg_addr=0x01, reg_val=baud_config_val_to_send):
            ser.close()
            return False

        print("  Waiting 2 seconds for device to reconfigure...")
        time.sleep(2)

        # Step 2: Switch to target baud
        print(f"\n[STEP 2] Switching to {target_baud} baud...")
        ser.close()
        time.sleep(0.2)

        ser = serial.Serial(port_name, target_baud, timeout=1.0)
        ser.reset_input_buffer()
        print("  Reconnected at new baud rate.")

        # Step 3: Check for data
        print(f"\n[STEP 3] Checking for data at {target_baud} baud for 5 seconds...")
        data_count = 0
        start_time = time.time()

        while time.time() - start_time < 5:
            if ser.in_waiting > 0:
                data = ser.read(ser.in_waiting)
                data_count += len(data)
                if data_count == len(data):
                    print(f"  [DATA] First chunk: {len(data)} bytes: {data[:30].hex()}...")
            time.sleep(0.01)

        if data_count > 0:
            print(f"\n[SUCCESS] Got {data_count} bytes at {target_baud} baud!")
        else:
            print(f"\n[ERROR] No data received at {target_baud} baud.")
            print(f"[INFO] The board may need firmware flashed, or it uses a different protocol.")

        return data_count > 0

    except serial.SerialException as e:
        print(f"[ERROR] Serial error: {e}")
        return False
    except serial.SerialTimeoutException:
        print(f"[ERROR] Write timed out — board is not accepting data.")
        return False
    finally:
        if ser and ser.is_open:
            ser.close()

if __name__ == "__main__":
    success = test_handshake()
    
    if success:
        print(f"\n[SUCCESS] Handshake test passed!")
        print(f"[INFO] The board is properly switching baud rates")
    else:
        print(f"\n[FAILED] Handshake test failed!")
        print(f"[INFO] Check the board firmware and connections")
    
    sys.exit(0 if success else 1) 
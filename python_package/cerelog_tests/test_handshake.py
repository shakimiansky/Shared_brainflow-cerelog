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
    
    ser.flush()  # Ensure packet is sent
    print(f"[SUCCESS] Sent {result} bytes")
    
    return True

def test_handshake():
    """Test the handshake process with the correct timing for macOS."""
    
    # Determine port and baud rates
    if platform.system() == 'Darwin':  # macOS
        port_name = '/dev/cu.usbserial-110' # IMPORTANT: Make sure this is your actual port name
    elif platform.system() == 'Windows':
        port_name = 'COM4' # IMPORTANT: Make sure this is your actual port name
    else:
        port_name = '/dev/ttyUSB0'

    initial_baud = 9600
    target_baud = 115200 # NOTE: The original test had a typo (115000). Use the standard 115200.
    
    # This should match the baud_config_val sent in the handshake
    # 0x04 = 115200, 0x05 = 230400
    baud_config_val_to_send = 0x04 

    print(f"[TEST] Manual Handshake Test (macOS Corrected)")
    print(f"[INFO] Port: {port_name}, Initial Baud: {initial_baud}, Target Baud: {target_baud}")
    print("-" * 50)
    
    ser = None
    try:
        # Step 1: Connect and WAIT for the board to boot
        print(f"[STEP 1] Connecting at {initial_baud} baud...")
        ser = serial.Serial(port_name, initial_baud, timeout=1.0)
        
        # <<< FIX #1: THE CRITICAL 5-SECOND BOOT WAIT >>>
        # Opening the port resets the board. We MUST wait for it to be ready.
        print("[INFO] Port opened. Waiting 5 seconds for board to boot...")
        time.sleep(5)
        print(f"[SUCCESS] Connected to {ser.name}")
        
        # Step 2: Send handshake packet
        print(f"\n[STEP 2] Sending handshake packet...")
        if not send_handshake_packet(ser, reg_addr=0x01, reg_val=baud_config_val_to_send):
            ser.close()
            return False
        
        # <<< FIX #2: THE CRITICAL DEVICE RECONFIGURATION WAIT >>>
        # We must give the board time to process the command and switch its baud rate.
        print("[INFO] Handshake sent. Waiting 2 seconds for device to reconfigure...")
        time.sleep(2)
        
        # Step 3: Switch host to target baud rate using the "close and re-open" method
        print(f"\n[STEP 3] Switching host to {target_baud} baud...")
        ser.close()
        time.sleep(0.2) # Brief pause for OS to release the port
        
        ser = serial.Serial(port_name, target_baud, timeout=1.0)
        ser.reset_input_buffer()
        print("[SUCCESS] Host reconnected at new baud rate.")
        
        # Step 4: Check for data at the new baud rate
        print(f"\n[STEP 4] Checking for data at {target_baud} baud for 3 seconds...")
        data_count = 0
        start_time = time.time()
        
        while time.time() - start_time < 3:
            if ser.in_waiting > 0:
                data = ser.read(ser.in_waiting)
                data_count += len(data)
                # Only print the first chunk of data to avoid spamming the console
                if data_count == len(data):
                    print(f"[DATA] Received first chunk of {len(data)} bytes: {data[:20].hex()}...")
            time.sleep(0.01) # Small sleep to prevent busy-waiting
        
        if data_count > 0:
            print(f"\n[SUCCESS] Received a total of {data_count} bytes at {target_baud} baud!")
            print(f"[SUCCESS] Handshake and baud rate switching worked!")
        else:
            print(f"\n[ERROR] No data received at {target_baud} baud.")
            print(f"[INFO] This indicates the handshake failed.")
        
        return data_count > 0
        
    except serial.SerialException as e:
        print(f"[ERROR] Serial error: {e}")
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
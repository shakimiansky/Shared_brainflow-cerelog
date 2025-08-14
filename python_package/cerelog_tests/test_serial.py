#This test is trash and always fails!!!! Bc pyserial sucks or it was written poorly





#!/usr/bin'env python3
"""
A Genuinely Useful Raw Serial Test for the Cerelog X8 Board

This script is designed to be a reliable, standalone diagnostic. It mimics the
successful behavior of the C++ driver by:
1. Waiting patiently for the board to reset after the serial port is opened.
2. Persistently sending the handshake command to ensure it is received.
3. Performing the full high-speed switch and data validation.

This test should PASS when run by itself if the hardware and firmware are correct.
"""

import platform
import serial
import serial.tools.list_ports
import time
import sys
import struct
from typing import Optional

# --- Constants ---
PACKET_TOTAL_SIZE = 37
START_MARKER_BYTES = b'\xab\xcd'

def find_serial_port() -> Optional[str]:
    """Scans for and returns the most likely serial port."""
    ports = list(serial.tools.list_ports.comports())
    print("[INFO] Available serial ports:")
    for port in ports:
        print(f"   - {port.device}: {port.description}")
        
    for port in ports:
        if 'usbserial' in port.device.lower():
            print(f"[INFO] Auto-detected target port: {port.device}")
            return port.device
    
    if ports:
        print(f"[WARN] No specific USB serial port found. Using the first available port: {ports[0].device}")
        return ports[0].device
        
    return None

def create_handshake_packet(baud_config_val: int) -> bytes:
    """Creates the 12-byte handshake packet with the mathematically correct checksum."""
    packet = bytearray(12)
    timestamp = int(time.time())

    packet[0] = 0xAA
    packet[1] = 0xBB
    packet[2] = 0x02 # Message Type: Timestamp
    
    packet[3:7] = struct.pack('>I', timestamp) # Timestamp (4 bytes, Big Endian)
    
    packet[7] = 0x01  # Register Address: Baud Rate
    packet[8] = baud_config_val
    
    # The correct checksum is the sum of bytes from index 2 up to (not including) 9
    packet[9] = sum(packet[2:9]) & 0xFF
    
    packet[10] = 0xCC
    packet[11] = 0xDD
    
    return bytes(packet)

def verify_packet(packet: bytes) -> bool:
    """Verifies a 37-byte chunk for checksum and end marker."""
    if len(packet) != PACKET_TOTAL_SIZE: return False
    if packet[35:37] != b'\xdc\xba': return False # Check End Marker
    calculated_checksum = sum(packet[2:34]) & 0xFF
    received_checksum = packet[34]
    return calculated_checksum == received_checksum

def run_standalone_diagnostic():
    """Runs the full diagnostic test, designed to work reliably on its own."""
    port_name = find_serial_port()
    if not port_name:
        print("\n[FAIL] No serial ports found. Cannot run test.")
        return False

    # --- Step 1: Connect and WAIT ---
    print("\n--- STEP 1: Initial Connection ---")
    print(f"[INFO] Opening port {port_name} at 9600 baud...")
    try:
        ser = serial.Serial(port_name, 9600, timeout=1.0)
        # THIS IS THE MOST IMPORTANT STEP. It mimics the C++ driver's successful behavior.
        # Opening the port resets the board. We MUST wait for it to boot.
        print("[INFO] Port opened. Waiting 5 seconds for board to reset and boot...")
        time.sleep(5)
        print("[PASS] Initial connection successful.")
    except serial.SerialException as e:
        print(f"\n[FAIL] Could not open serial port: {e}")
        return False

    # --- Step 2: Persistent Handshake ---
    print("\n--- STEP 2: Handshake and Baud Rate Negotiation ---")
    target_baud_rate = 230400 if platform.system() == 'Darwin' else 921600
    baud_config_val = 0x05 if platform.system() == 'Darwin' else 0x07
    
    handshake_packet = create_handshake_packet(baud_config_val)
    print(f"[INFO] Target baud rate for {platform.system()}: {target_baud_rate}")
    
    # To overcome timing issues, we will send the handshake multiple times.
    print("[INFO] Persistently sending handshake command (3 attempts)...")
    for i in range(3):
        print(f"   Attempt {i+1}/3... Writing {len(handshake_packet)} bytes.")
        ser.write(handshake_packet)
        time.sleep(0.2)
    print("[INFO] Handshake attempts complete.")

    # --- Step 3: Switch Host to High Speed ---
    print("\n--- STEP 3: Reconfiguring Host to High Speed ---")
    print(f"[INFO] Switching host to {target_baud_rate} baud...")
    ser.baudrate = target_baud_rate
    ser.reset_input_buffer()
    print("[PASS] Host is now listening at high speed.")

    # --- Step 4: Validate High-Speed Data ---
    print("\n--- STEP 4: Validating High-Speed Data Stream ---")
    buffer = bytearray()
    found_packets = 0
    start_time = time.time()
    
    print("[INFO] Listening for data for 5 seconds...")
    while time.time() - start_time < 5:
        if ser.in_waiting > 0:
            buffer.extend(ser.read(ser.in_waiting))
        
        while True:
            start_index = buffer.find(START_MARKER_BYTES)
            if start_index == -1 or len(buffer) < start_index + PACKET_TOTAL_SIZE:
                break

            packet = buffer[start_index : start_index + PACKET_TOTAL_SIZE]
            if verify_packet(packet):
                found_packets += 1
                if found_packets == 1:
                    print(f"[SUCCESS] Received first valid high-speed data packet at {time.time() - start_time:.2f}s!")
                buffer = buffer[start_index + PACKET_TOTAL_SIZE:]
            else:
                buffer = buffer[start_index + 1:]

    ser.close()
    print("[INFO] Test finished. Serial port closed.")

    # --- Step 5: Final Results ---
    print("\n" + "="*40)
    print("=== FINAL RESULTS ===")
    duration = time.time() - start_time
    if found_packets > 0:
        rate = found_packets / duration
        print(f"[SUCCESS] Test PASSED!")
        print(f"   - Found {found_packets} valid packets.")
        print(f"   - Measured data rate: {rate:.1f} packets/sec (Expected: ~500)")
        return True
    else:
        print("[FAIL] Test FAILED.")
        print("   - No valid data packets were received at the high baud rate.")
        print("   - This indicates a persistent failure in the handshake or data generation.")
        return False

if __name__ == "__main__":
    if run_standalone_diagnostic():
        sys.exit(0)
    else:
        sys.exit(1)
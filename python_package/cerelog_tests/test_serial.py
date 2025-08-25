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
    """
    Runs the full diagnostic test, incorporating the robust connection logic
    discovered during C++ driver development for macOS compatibility.
    """
    port_name = find_serial_port()
    if not port_name:
        print("\n[FAIL] No serial ports found. Cannot run test.")
        return False

    ser = None # Initialize ser to None for the finally block
    try:
        # --- Step 1: Connect, WAIT, and Send Handshake at 9600 ---
        print("\n--- STEP 1: Initial Connection & Handshake ---")
        print(f"[INFO] Opening port {port_name} at 9600 baud...")
        ser = serial.Serial(port_name, 9600, timeout=1.0)
        print("[INFO] Port opened. Waiting 5 seconds for board to reset and boot...")
        time.sleep(5)

        target_baud_rate = 115200
        baud_config_val = 0x04
        handshake_packet = create_handshake_packet(baud_config_val)
        
        print(f"[INFO] Sending handshake to switch device to {target_baud_rate} baud...")
        ser.write(handshake_packet)

        # <<< FIX #1: THE DEVICE WAIT >>>
        # We MUST wait for the device to process the command and switch its baud rate.
        # 2 seconds is a safe, reliable delay.
        print("[INFO] Handshake sent. Waiting 2 seconds for device to reconfigure...")
        time.sleep(2)
        
        # --- Step 2: The macOS Driver Reset Strategy ---
        print("\n--- STEP 2: Reconfiguring Host to High Speed (macOS Safe Method) ---")
        
        # <<< FIX #2: THE DRIVER RESET STRATEGY >>>
        # We proved in C++ that changing baud rate on an open port hangs the macOS driver.
        # The only reliable method is to close and re-open the port at the new speed.
        print("[INFO] Closing port to reset driver state...")
        ser.close()
        time.sleep(0.2) # Brief pause for the OS to release the port handle

        print(f"[INFO] Re-opening port at new speed: {target_baud_rate} baud...")
        ser = serial.Serial(port_name, target_baud_rate, timeout=1.0)
        ser.reset_input_buffer()
        print("[PASS] Host is now listening at high speed.")

        # --- Step 3: Validate High-Speed Data ---
        print("\n--- STEP 3: Validating High-Speed Data Stream ---")
        buffer = bytearray()
        found_packets = 0
        start_time = time.time()
        
        print("[INFO] Listening for data for 5 seconds...")
        # Main validation loop remains the same
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

    except serial.SerialException as e:
        print(f"\n[FAIL] A serial port error occurred: {e}")
        return False
    finally:
        if ser and ser.is_open:
            ser.close()
            print("[INFO] Test finished. Serial port closed.")

    # --- Step 4: Final Results ---
    print("\n" + "="*40)
    print("=== FINAL RESULTS ===")
    if found_packets > 0:
        # Measure duration only over the 5-second listening window
        rate = found_packets / 5.0
        print(f"[SUCCESS] Test PASSED!")
        print(f"   - Found {found_packets} valid packets.")
        print(f"   - Measured data rate: ~{rate:.1f} packets/sec (Expected: ~500)")
        return True
    else:
        print("[FAIL] Test FAILED.")
        print("   - No valid data packets were received at the high baud rate.")
        return False

if __name__ == "__main__":
    if run_standalone_diagnostic():
        sys.exit(0)
    else:
        sys.exit(1)
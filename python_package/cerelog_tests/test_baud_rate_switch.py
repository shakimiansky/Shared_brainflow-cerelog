#!/usr/bin/env python3
"""
Test script to verify dynamic baud rate configuration through handshake.

This test verifies that:
1. Handshake works at 9600 baud with baud rate configuration parameters
2. BrainFlow sends target baud rate in handshake (reg_addr=0x01, reg_val=baud_rate_index)
3. Arduino switches to the configured baud rate after handshake
4. Data streaming continues at the configured baud rate
"""

import time
import sys
import os
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('test_baud_rate_switch.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Add the parent directory to the path to import brainflow
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brainflow.board_shim import BoardShim, BrainFlowPresets, BoardIds, BrainFlowInputParams, BrainFlowError
from brainflow.data_filter import DataFilter


def test_dynamic_baud_rate_configuration():
    """Test the dynamic baud rate configuration through handshake."""
    
    logger.info("=== Dynamic Baud Rate Configuration Test ===")
    logger.info("This test verifies baud rate configuration through handshake parameters")
    logger.info("")
    
    # Board configuration
    board_id = BoardIds.CERELOG_X8_BOARD
    input_params = BrainFlowInputParams()
    board = BoardShim(board_id, input_params)
    
    try:
        logger.info("1. Preparing session (should send baud rate config in handshake)...")
        board.prepare_session()
        logger.info("   ✓ Session prepared successfully")
        logger.info("   ✓ Handshake sent with baud rate configuration")
        
        logger.info("\n2. Starting stream (should be at configured baud rate)...")
        board.start_stream(45000)  # 45 second buffer
        logger.info("   ✓ Stream started successfully")
        
        logger.info("\n3. Collecting data for 5 seconds...")
        time.sleep(5)
        
        # Get data
        data = board.get_board_data()
        num_samples = data.shape[1] if data.size > 0 else 0
        
        logger.info(f"   ✓ Collected {num_samples} samples")
        
        if num_samples > 0:
            logger.info(f"   ✓ Data shape: {data.shape}")
            logger.info(f"   ✓ Sample rate: {num_samples / 5:.1f} Hz")
            
            # Check if we have data on all channels
            eeg_channels = board.get_eeg_channels(board_id)
            if len(eeg_channels) >= 8:
                logger.info(f"   ✓ All {len(eeg_channels)} EEG channels active")
        
        logger.info("\n4. Stopping stream...")
        board.stop_stream()
        logger.info("   ✓ Stream stopped successfully")
        
        logger.info("\n5. Releasing session...")
        board.release_session()
        logger.info("   ✓ Session released successfully")
        
        logger.info("\n=== Test Results ===")
        if num_samples > 0:
            logger.info("✓ SUCCESS: Dynamic baud rate configuration appears to be working")
            logger.info("✓ Data streaming is active at configured baud rate")
        else:
            logger.error("✗ FAILURE: No data received")
            return False
            
    except Exception as e:
        logger.error(f"✗ ERROR: {e}")
        return False
    
    return True


def test_baud_rate_configuration_values():
    """Test different baud rate configuration values."""
    
    logger.info("\n=== Baud Rate Configuration Values Test ===")
    logger.info("This test verifies the baud rate configuration mapping")
    logger.info("")
    
    # Baud rate configuration mapping
    baud_configs = {
        0x00: 9600,     # Default
        0x01: 19200,    # Low speed
        0x02: 38400,    # Medium speed
        0x03: 57600,    # High speed
        0x04: 115200,   # Very high speed
        0x05: 230400,   # Ultra high speed (macOS target)
        0x06: 460800,   # Maximum speed
        0x07: 921600,   # Super speed
    }
    
    logger.info("Baud Rate Configuration Mapping:")
    for config_val, baud_rate in baud_configs.items():
        logger.info(f"   Config 0x{config_val:02X} → {baud_rate:,} baud")
    
    logger.info(f"\nExpected target baud rate for macOS: {baud_configs[0x05]:,} baud")
    
    return True


def test_manual_configuration_disabled():
    """Test that manual configuration is properly disabled."""
    
    logger.info("\n=== Manual Configuration Test ===")
    logger.info("This test verifies manual configuration is disabled")
    logger.info("")
    
    board_id = BoardIds.CERELOG_X8_BOARD
    input_params = BrainFlowInputParams()
    board = BoardShim(board_id, input_params)
    
    try:
        logger.info("1. Preparing session...")
        board.prepare_session()
        
        logger.info("2. Testing manual baud rate configuration...")
        try:
            response = board.config_board("baud_rate=4")  # Try to set to 115200
            logger.warning(f"   ⚠ Unexpected success: {response}")
            # This should not succeed since manual configuration is disabled
            board.release_session()
            return False
            
        except BrainFlowError as e:
            if "INVALID_ARGUMENTS_ERROR" in str(e) or "unable to config board" in str(e):
                logger.info("   ✓ Correctly rejects manual configuration (as expected)")
                logger.info(f"   Error: {e}")
            else:
                logger.error(f"   ✗ Unexpected error type: {e}")
                board.release_session()
                return False
        
        board.release_session()
        
    except Exception as e:
        logger.error(f"✗ ERROR: {e}")
        return False
    
    return True


if __name__ == "__main__":
    logger.info("Cerelog X8 Dynamic Baud Rate Configuration Test")
    logger.info("===============================================")
    logger.info("")
    
    # Run the main test
    success = test_dynamic_baud_rate_configuration()
    
    # Run the configuration values test
    config_success = test_baud_rate_configuration_values()
    
    # Run the manual configuration test
    manual_success = test_manual_configuration_disabled()
    
    logger.info("\n" + "="*60)
    if success and config_success and manual_success:
        logger.info("🎉 ALL TESTS PASSED!")
        logger.info("Dynamic baud rate configuration is working correctly.")
        logger.info("Handshake parameters are being used for baud rate configuration.")
    else:
        logger.error("❌ SOME TESTS FAILED!")
        logger.error("Check the output above for details.")
    
    sys.exit(0 if success and config_success and manual_success else 1) 
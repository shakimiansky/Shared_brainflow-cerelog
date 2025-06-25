import platform
import numpy as np
from brainflow.board_shim import BoardShim, BrainFlowInputParams, BoardIds, BrainFlowError, LogLevels
import time
import csv
from typing import List, Tuple
import logging
import sys

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('test_validate_eeg.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

def calculate_rms(signal: np.ndarray) -> float:
    """Calculate Root Mean Square of a signal."""
    return np.sqrt(np.mean(np.square(signal)))

def calculate_cmrr(signals: List[np.ndarray]) -> float:
    """
    Calculate Common Mode Rejection Ratio (CMRR).
    CMRR = 20 * log10(differential_mode_gain / common_mode_gain)
    """
    if len(signals) < 2:
        raise ValueError("Need at least 2 signals to calculate CMRR")
    
    # Calculate common mode signal (average of all channels)
    common_mode = np.mean(signals, axis=0)
    
    # Calculate differential mode (difference between channels)
    differential_mode = signals[0] - signals[1]  # Using first two channels
    
    # Calculate gains
    common_mode_gain = np.std(common_mode)
    differential_mode_gain = np.std(differential_mode)
    
    if common_mode_gain == 0:
        return float('inf')
    
    return 20 * np.log10(differential_mode_gain / common_mode_gain)

def validate_voltage_range(signal: np.ndarray, expected_min: float = -100, expected_max: float = 100) -> bool:
    """Validate if signal is within expected voltage range (in microvolts)."""
    return np.all((signal >= expected_min) & (signal <= expected_max))

def run_validation_tests():
    params = BrainFlowInputParams()
    
    # Port scanning will automatically detect the correct port
    logger.info(f"Using port scanning on {platform.system()}")
    
    test_duration = 5  # seconds
    results = {
        "rms_tests": [],
        "voltage_range_tests": [],
        "cmrr_tests": []
    }
    
    try:
      # Initialize board
      board = BoardShim(BoardIds.CERELOG_X8_BOARD, params)
      BoardShim.enable_dev_board_logger()
      BoardShim.set_log_level(LogLevels.LEVEL_DEBUG.value)
      BoardShim.set_log_file('test_validate_eeg.log')
        
      # Get board configuration
      sample_rate = BoardShim.get_sampling_rate(BoardIds.CERELOG_X8_BOARD)
      eeg_channels = BoardShim.get_eeg_channels(BoardIds.CERELOG_X8_BOARD)
        
      logger.info("Starting validation tests...")
      logger.info(f"Sample rate: {sample_rate} SPS")
      logger.info(f"EEG Channels: {eeg_channels}")
      
      # Prepare and start session
      board.prepare_session()
      board.start_stream()
      logger.info(f"Collecting data for {test_duration} seconds...")
      
      time.sleep(test_duration)
      board.stop_stream()
      data = board.get_board_data()
      
      if data.size == 0:
            raise ValueError("No data collected during test")
            
    # 1. RMS Tests
      logger.info("\n=== RMS Tests ===")
      for ch in eeg_channels:
            ch_data = data[ch]
            rms = calculate_rms(ch_data)
            results["rms_tests"].append({
                  "channel": ch,
                  "rms_value": rms,
                  "passed": 0.1 <= rms <= 50  # Expected RMS range in microvolts
            })
            logger.info(f"Channel {ch} RMS: {rms:.4f} µV")
            
    # 2. Voltage Range Tests
      logger.info("\n=== Voltage Range Tests ===")
      for ch in eeg_channels:
            ch_data = data[ch]
            passed = validate_voltage_range(ch_data)
            results["voltage_range_tests"].append({
                "channel": ch,
                "passed": passed
            })
            logger.info(f"Channel {ch} voltage range test: {'PASSED' if passed else 'FAILED'}")
        
    # 3. CMRR Tests
      logger.info("\n=== CMRR Tests ===")
      if len(eeg_channels) >= 2:
            signals = [data[ch] for ch in eeg_channels[:2]]  # Use first two channels
            cmrr = calculate_cmrr(signals)
            results["cmrr_tests"].append({
                "channels": eeg_channels[:2],
                "cmrr_value": cmrr,
                "passed": cmrr >= 60  # Typical CMRR should be at least 60 dB
            })
            logger.info(f"CMRR between channels {eeg_channels[:2]}: {cmrr:.2f} dB")
        
        # Save results to CSV
      with open('validation_results.csv', mode='w', newline='') as file:
            writer = csv.writer(file)
            writer.writerow(['Test Type', 'Channel', 'Value', 'Passed'])
            
            for test_type, test_results in results.items():
                for result in test_results:
                    if 'channel' in result:
                        writer.writerow([
                            test_type,
                            result['channel'],
                            result.get('rms_value', result.get('cmrr_value', 'N/A')),
                            result['passed']
                        ])
        
      board.release_session()
      logger.info("\nValidation tests completed. Results saved to 'validation_results.csv'")
        
    except BrainFlowError as e:
        logger.error(f"Error during validation: {e}")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")

if __name__ == "__main__":
    run_validation_tests() 
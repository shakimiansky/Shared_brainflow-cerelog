import platform
import time
import numpy as np
from brainflow.board_shim import BoardShim, BrainFlowInputParams, BoardIds, BrainFlowError, LogLevels
import faulthandler
import csv
import logging
import sys
faulthandler.enable()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('test_brainflow.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

def calculate_rms(signal):
    return np.sqrt(np.mean(np.square(signal)))

def calculate_peak_to_peak(signal):
    return np.max(signal) - np.min(signal)

def test_my_board():
    params = BrainFlowInputParams()    
    logger.info(f"Using port scanning on {platform.system()} (will auto-detect port)")
    
    params.timeout = 25
    time_len = 5 # collect data for _ seconds
    try:
        board = BoardShim(BoardIds.CERELOG_X8_BOARD, params)
        BoardShim.enable_dev_board_logger()
        BoardShim.set_log_level(LogLevels.LEVEL_DEBUG.value)
        BoardShim.set_log_file('test_brainflow.log')
        sample_rate = BoardShim.get_sampling_rate(BoardIds.CERELOG_X8_BOARD)
        eeg_channels = BoardShim.get_eeg_channels(BoardIds.CERELOG_X8_BOARD)
        logger.info(f"Sample rate  : {sample_rate} SPS")
        logger.info(f"EEG Channels : {eeg_channels}")

        board.prepare_session()
        logger.info("✓ Session prepared successfully")

        board.start_stream()
        logger.info("... Stream started for {} seconds".format(time_len))

        time.sleep(time_len)  # Collect some data
        board.stop_stream()
        logger.info("Stream time completed")
        data = board.get_board_data()

        logger.info(f"Data shape: {data.shape}")
        logger.info(f"✓ Got {data.shape[1]} samples")

        # Calculate RMS for each EEG channel
        if data.size > 0:
            for ch in eeg_channels:
                ch_data = data[ch]
                rms = calculate_rms(ch_data)
                logger.info(f"RMS of EEG channel {ch}: {rms:.4f} V")

        # Calculate and print average Vpp of the channels
        vpp_values = []
        for ch in eeg_channels:
            ch_data = data[ch]
            ptp = calculate_peak_to_peak(ch_data)
            if ptp >= 0.01:  # Exclude channels with Vpp less than 0.01
                vpp_values.append(ptp)
        if vpp_values:
            avg_vpp = np.mean(vpp_values)
            logger.info(f"Average Vpp of EEG channels: {avg_vpp:.4f} V")
        else:
            logger.info("No channels with Vpp >= 0.01 to calculate average Vpp.")
        # Write data to CSV
        with open('data.csv', mode='w', newline='') as file:
            writer = csv.writer(file)
            writer.writerow(['Time'] + [f"Channel {ch}" for ch in eeg_channels])
            for i in range(data.shape[1]):
                writer.writerow([i] + [f"{data[ch][i]:.4f}" for ch in eeg_channels])

        board.release_session()
        logger.info("✓ Done!")

    except BrainFlowError as e:
        logger.error(f"✗ Error: {e}")

if __name__ == "__main__":
    test_my_board()
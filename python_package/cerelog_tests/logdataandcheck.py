import time
import numpy as np
from brainflow.board_shim import BoardShim, BrainFlowInputParams, BoardIds

# --- Configuration ---
BOARD_ID = BoardIds.CERELOG_X8_BOARD
DURATION_SECONDS = 30  # Log data for 30 seconds to capture several events
# We will log the first EEG channel. BrainFlow's eeg_channels list is 1-indexed.
LOG_CHANNEL_ID = BoardShim.get_eeg_channels(BOARD_ID)[0]
OUTPUT_FILENAME = "channel_1_raw_data.log"

def main():
    """
    Connects to the Cerelog board, streams data for a fixed duration,
    and writes every single raw sample from a specific channel to a log file.
    """
    params = BrainFlowInputParams()
    params.timeout = 15
    board = BoardShim(BOARD_ID, params)

    try:
        print(f"Connecting to {board.get_board_descr(BOARD_ID)['name']}...")
        board.prepare_session()

        print(f"\nStarting stream. Logging ALL raw data from Channel {LOG_CHANNEL_ID} for {DURATION_SECONDS} seconds...")
        print("Please wait, do not close this window...")
        
        # Start the stream with a large enough buffer
        sampling_rate = BoardShim.get_sampling_rate(BOARD_ID)
        board.start_stream(int(DURATION_SECONDS * sampling_rate * 1.5))

        # Wait for the full duration
        time.sleep(DURATION_SECONDS)

        # Stop the stream and get all the data that was collected
        board.stop_stream()
        print("Stream stopped. Fetching all data from the buffer...")
        data = board.get_board_data()
        
        if data.size == 0:
            print("\nERROR: No data was collected. Something is wrong.")
            return

        print(f"Collected a total of {data.shape[1]} samples.")

        # Extract the specific channel data and convert from Volts to microVolts
        channel_data_uV = data[LOG_CHANNEL_ID] * 1e6

        # Write every single sample to the output file
        print(f"Writing {len(channel_data_uV)} samples to '{OUTPUT_FILENAME}'...")
        with open(OUTPUT_FILENAME, 'w') as f:
            f.write(f"# Raw, unfiltered data dump from Channel {LOG_CHANNEL_ID}\n")
            f.write(f"# Total Samples: {len(channel_data_uV)}\n")
            f.write("# ---\n")
            for sample in channel_data_uV:
                f.write(f"{sample:.4f}\n")
        
        print("\nSUCCESS: Data dump complete.")
        print(f"You can now open '{OUTPUT_FILENAME}' in a text editor to inspect every value.")

    except Exception as e:
        print(f"\nAN ERROR OCCURRED: {e}")
    finally:
        if board and board.is_prepared():
            print("Releasing session...")
            board.release_session()
            print("Session released.")

if __name__ == "__main__":
    main()
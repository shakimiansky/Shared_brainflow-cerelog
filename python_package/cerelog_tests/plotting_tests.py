import time
import numpy as np
import matplotlib.pyplot as plt

from brainflow.board_shim import BoardShim, BrainFlowInputParams, BoardIds, BrainFlowError

def main():
    """
    A simple script to connect to a BCI, collect data for a few seconds,
    and then plot the EEG data.
    """
    # Board and acquisition parameters
    board_id = BoardIds.CERELOG_X8_BOARD
    duration_seconds = 10

    # --- 1. SETUP THE BOARD ---
    # BrainFlow uses a params object to configure the board
    params = BrainFlowInputParams()



    # ADD THIS LINE - THIS IS THE FIX
    params.timeout = 10  # Set a generous timeout for stable data reading

    
    # Create the BoardShim object
    board = BoardShim(board_id, params)

    # Use a try/finally block to ensure session is always released
    try:
        # Get board info using static methods
        eeg_channels = BoardShim.get_eeg_channels(board_id)
        timestamp_channel = BoardShim.get_timestamp_channel(board_id)
        sampling_rate = BoardShim.get_sampling_rate(board_id)

        print(f"Connecting to {board.get_board_descr(board_id)['name']}...")
        print(f"EEG Channels: {eeg_channels}")
        print(f"Sampling Rate: {sampling_rate} Hz")

        # Prepare the session (finds the board and establishes connection)
        board.prepare_session()

        # --- 2. ACQUIRE DATA ---
        print(f"\nStarting stream for {duration_seconds} seconds...")
        board.start_stream()
        time.sleep(duration_seconds)
        
        # Stop the stream and get the data from the internal buffer
        board.stop_stream()
        print("Stream stopped. Getting data...")
        data = board.get_board_data()

        if data.size == 0:
            print("Error: No data was collected.")
            return

        # --- 3. PROCESS AND PLOT DATA ---
        # Get the specific data streams from the data array
        eeg_data = data[eeg_channels]
        timestamps = data[timestamp_channel]

        # Create a time axis starting from 0
        time_axis = timestamps - timestamps[0]

        print("Plotting data...")
        plt.figure(figsize=(15, 8))

        # Plot each EEG channel with a vertical offset for clarity
        offset_value = 150 # µV
        for i, channel_data in enumerate(eeg_data):
            plt.plot(time_axis, channel_data + i * offset_value, label=f'Channel {eeg_channels[i]}')

        # Add titles and labels for clarity
        plt.title(f'{duration_seconds} Seconds of Cerelog EEG Data')
        plt.xlabel('Time (s)')
        plt.ylabel('Voltage (µV) - Channels are offset for clarity')
        plt.legend(loc='upper right')
        plt.grid(True)
        plt.tight_layout()

        # Show the plot
        plt.show()

    except BrainFlowError as e:
        print(f"BrainFlow Error: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
    finally:
        # --- 4. CLEAN UP ---
        # Always release the session to free the COM port
        if board.is_prepared():
            print("\nReleasing session.")
            board.release_session()

if __name__ == "__main__":
    main()
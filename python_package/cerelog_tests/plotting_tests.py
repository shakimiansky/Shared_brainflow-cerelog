import time
import numpy as np
import matplotlib.pyplot as plt

from brainflow.board_shim import BoardShim, BrainFlowInputParams, BoardIds, BrainFlowError

def main():
    """
    Connects to a BCI, collects a significant amount of data, and plots each
    EEG channel in a clean, professional subplot layout for clear analysis.
    """
    # Board and acquisition parameters
    board_id = BoardIds.CERELOG_X8_BOARD
    duration_seconds = 30  # Increased to 30 seconds for more data points

    # --- 1. SETUP THE BOARD ---
    params = BrainFlowInputParams()
    params.timeout = 15  # A slightly longer timeout for more robust streaming
    
    board = BoardShim(board_id, params)

    try:
        eeg_channels = BoardShim.get_eeg_channels(board_id)
        timestamp_channel = BoardShim.get_timestamp_channel(board_id)
        sampling_rate = BoardShim.get_sampling_rate(board_id)

        print(f"Connecting to {board.get_board_descr(board_id)['name']}...")
        print(f"EEG Channels: {eeg_channels}")
        print(f"Sampling Rate: {sampling_rate} Hz")

        board.prepare_session()

        # --- 2. ACQUIRE DATA ---
        print(f"\nStarting stream for {duration_seconds} seconds...")
        board.start_stream()
        time.sleep(duration_seconds)
        
        board.stop_stream()
        print("Stream stopped. Getting data...")
        data = board.get_board_data()

        if data.size == 0:
            print("Error: No data was collected.")
            return

        # --- 3. PROCESS AND PLOT DATA ---
        eeg_data = data[eeg_channels]
        timestamps = data[timestamp_channel]

        # CRITICAL: Convert data from Volts (V) to Microvolts (µV) for standard EEG plotting
        eeg_data *= 1e6

        # Create a time axis starting from 0
        time_axis = timestamps - timestamps[0]

        print("Plotting data in a 4x2 subplot grid...")

        # Create a figure and a grid of subplots (4 rows, 2 columns)
        # `sharex=True` ensures all subplots share the same time axis
        fig, axes = plt.subplots(4, 2, figsize=(18, 10), sharex=True)
        
        # Add a main title for the entire figure
        fig.suptitle(f'{duration_seconds} Seconds of Cerelog EEG Data', fontsize=16)

        # Flatten the 2D array of axes for easy iteration
        axes = axes.flatten()

        # Plot each EEG channel on its own subplot
        for i, channel_id in enumerate(eeg_channels):
            ax = axes[i]
            ax.plot(time_axis, eeg_data[i], linewidth=0.8)
            ax.set_title(f'Channel {channel_id}')
            ax.set_ylabel('Voltage (µV)')
            ax.grid(True)
        
        # Add a shared X-axis label to the bottom of the figure
        fig.text(0.5, 0.04, 'Time (s)', ha='center', va='center')
        
        # Adjust layout to prevent titles and labels from overlapping
        plt.tight_layout(rect=[0, 0.05, 1, 0.96])

        # Show the plot
        plt.show()

    except BrainFlowError as e:
        print(f"BrainFlow Error: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
    finally:
        # --- 4. CLEAN UP ---
        if board.is_prepared():
            print("\nReleasing session.")
            board.release_session()

if __name__ == "__main__":
    main()
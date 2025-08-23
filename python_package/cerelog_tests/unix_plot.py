import time
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from brainflow.board_shim import BoardShim, BrainFlowInputParams, BoardIds, BrainFlowError

# --- Configuration ---
BOARD_ID = BoardIds.CERELOG_X8_BOARD
SECONDS_TO_DISPLAY = 10
UPDATE_INTERVAL_MS = 200
Y_AXIS_PADDING_FACTOR = 1.2

# --- Global variables ---
board = None
eeg_channels = []
timestamp_channel = -1 ## <-- MODIFIED: Added to store the timestamp channel index
sampling_rate = 0
window_size = 0
data_buffer = np.array([])
y_limits = {}

def main():
    """
    Connects to the Cerelog board and creates a robust, real-time, scrolling plot
    that uses the REAL board timestamps for the X-axis.
    """
    global board, eeg_channels, timestamp_channel, sampling_rate, window_size, data_buffer, y_limits

    params = BrainFlowInputParams()
    params.timeout = 15
    board = BoardShim(BOARD_ID, params)

    try:
        eeg_channels = BoardShim.get_eeg_channels(BOARD_ID)
        timestamp_channel = BoardShim.get_timestamp_channel(BOARD_ID) ## <-- MODIFIED: Get the timestamp channel
        sampling_rate = BoardShim.get_sampling_rate(BOARD_ID)
        window_size = SECONDS_TO_DISPLAY * sampling_rate

        for i in range(len(eeg_channels)):
            y_limits[i] = (-100, 100)

        print(f"Connecting to {board.get_board_descr(BOARD_ID)['name']}...")
        board.prepare_session()

        print("\nStarting stream... Close the plot window to stop.")

        buffer_size_samples = 2 * 60 * sampling_rate 
        board.start_stream(buffer_size_samples)

        time.sleep(2)

        initial_data = board.get_board_data()
        if initial_data.size > 0:
            data_buffer = initial_data
        else:
            num_board_channels = BoardShim.get_num_rows(BOARD_ID)
            data_buffer = np.empty((num_board_channels, 0))

        # --- Plot Setup ---
        fig, axes = plt.subplots(4, 2, figsize=(18, 10), sharex=True)
        fig.suptitle('Real-Time Cerelog EEG Waveforms (Using REAL Timestamps)', fontsize=16) ## <-- MODIFIED: Title
        axes_flat = axes.flatten()
        lines = [ax.plot([], [], lw=1)[0] for ax in axes_flat]

        for i, ax in enumerate(axes_flat):
            ax.set_title(f'Channel {eeg_channels[i]}')
            ax.set_ylabel('Voltage (µV)')
            ax.grid(True)
            # We no longer set X-limits here, as they will be dynamic based on timestamps

        fig.text(0.5, 0.04, 'Time (s, relative to start)', ha='center', va='center') ## <-- MODIFIED: X-axis label
        plt.tight_layout(rect=[0, 0.05, 1, 0.96])

        def on_close(event):
            print("Plot window closed, stopping stream...")
            if board and board.is_streaming():
                board.stop_stream()
                board.release_session()
            print("Session released. Exiting.")

        fig.canvas.mpl_connect('close_event', on_close)

        ani = FuncAnimation(fig, update_plot, fargs=(lines, axes_flat), interval=UPDATE_INTERVAL_MS, blit=False)
        plt.show()

    except Exception as e:
        print(f"An error occurred in main(): {e}")
    finally:
        if board and board.is_prepared():
            board.release_session()

def update_plot(frame, lines, axes):
    """
    This function is called periodically to update the plot data.
    This version now uses the REAL board timestamps.
    """
    global data_buffer, y_limits

    try:
        num_samples_available = board.get_board_data_count()

        if num_samples_available > 0:
            new_data = board.get_board_data(num_samples_available)
            print(f"Received {new_data.shape[1]} new samples. Total buffer size: {data_buffer.shape[1]}")
            data_buffer = np.hstack((data_buffer, new_data))
        else:
            return lines

        buffer_limit = int(window_size * 1.5) 
        if data_buffer.shape[1] > buffer_limit:
            data_buffer = data_buffer[:, -buffer_limit:]

        plot_data = data_buffer[:, -window_size:]
        
        if plot_data.shape[1] < window_size:
            return lines

        ## --- MODIFICATION START ---
        # Get the real EEG and Timestamp data
        eeg_plot_data = plot_data[eeg_channels] * 1e6
        timestamps = plot_data[timestamp_channel]
        
        # Create a relative time vector for a clean plot starting near 0
        # This makes the X-axis show "seconds since start" instead of a huge Unix time
        relative_time_vector = timestamps - timestamps[0]
        ## --- MODIFICATION END ---


        # Update each subplot
        for i, (line, ax) in enumerate(zip(lines, axes)):
            channel_data = eeg_plot_data[i]
            
            centered_data = channel_data - np.mean(channel_data)
            
            ## <-- MODIFIED: Use the real time vector
            line.set_data(relative_time_vector, centered_data)

            # Update the X-axis limits to scroll with the new time data
            ax.set_xlim(relative_time_vector[0], relative_time_vector[-1])

            # Adaptive Y-Axis Logic (unchanged)
            max_val = np.max(centered_data)
            min_val = np.min(centered_data)
            if np.isclose(max_val, min_val):
                max_val += 1; min_val -= 1
            
            target_max = max_val * Y_AXIS_PADDING_FACTOR
            target_min = min_val * Y_AXIS_PADDING_FACTOR
            
            current_min, current_max = y_limits[i]
            smoothing_factor = 0.1
            new_max = current_max * (1 - smoothing_factor) + target_max * smoothing_factor
            new_min = current_min * (1 - smoothing_factor) + target_min * smoothing_factor
            
            ax.set_ylim(new_min, new_max)
            y_limits[i] = (new_min, new_max)

    except Exception as e:
        print(f"!!! ERROR IN UPDATE_PLOT: {e}")

    return lines

if __name__ == "__main__":
    main()
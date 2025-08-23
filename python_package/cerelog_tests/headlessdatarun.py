import time
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from brainflow.board_shim import BoardShim, BrainFlowInputParams, BoardIds
from brainflow.data_filter import DataFilter, FilterTypes

# --- Configuration ---
BOARD_ID = BoardIds.CERELOG_X8_BOARD
SECONDS_TO_DISPLAY = 20
UPDATE_INTERVAL_MS = 1000 # 1 second, to prevent crashes without blitting

# --- Global variables ---
board = None
eeg_channels = []
sampling_rate = 0
window_size = 0
# This is our persistent local data buffer
data_buffer = np.array([])

def main():
    """
    Connects to the Cerelog board. This version correctly handles data buffering
    and filtering to prevent visual artifacts like overwriting.
    """
    global board, eeg_channels, sampling_rate, window_size, data_buffer

    params = BrainFlowInputParams()
    params.timeout = 15
    board = BoardShim(BOARD_ID, params)

    try:
        eeg_channels = BoardShim.get_eeg_channels(BOARD_ID)
        sampling_rate = BoardShim.get_sampling_rate(BOARD_ID)
        window_size = int(SECONDS_TO_DISPLAY * sampling_rate)

        # Initialize our local buffer with the correct number of rows
        num_board_channels = BoardShim.get_num_rows(BOARD_ID)
        data_buffer = np.empty((num_board_channels, 0))

        print(f"Connecting to {board.get_board_descr(BOARD_ID)['name']}...")
        board.prepare_session()

        print("\nStarting stream... Plot will fill for 20s then scroll (updates once/sec).")
        board.start_stream(120 * sampling_rate) # 2-minute internal buffer
        time.sleep(1)

        # --- Plot Setup ---
        fig, axes = plt.subplots(4, 2, figsize=(18, 10), sharex=True)
        fig.suptitle(f'Definitive Slow-Update Plot (Correct Buffering)', fontsize=16)
        axes_flat = axes.flatten()
        lines = [ax.plot([], [], lw=1)[0] for ax in axes_flat]

        for i, ax in enumerate(axes_flat):
            ax.set_title(f'Channel {eeg_channels[i]}')
            ax.set_ylabel('Voltage (µV)')
            ax.grid(True)
            ax.set_xlim(0, SECONDS_TO_DISPLAY)
            ax.set_ylim(-100, 100)

        fig.text(0.5, 0.04, 'Time (s)', ha='center', va='center')
        plt.tight_layout(rect=[0, 0.05, 1, 0.96])

        ani = FuncAnimation(fig, update_plot, fargs=(lines, axes_flat),
                            interval=UPDATE_INTERVAL_MS, blit=False)
        
        plt.show()

    except Exception as e:
        print(f"An error occurred in main(): {e}")
    finally:
        if board and board.is_prepared():
            print("Cleaning up session...")
            board.stop_stream()
            board.release_session()
            print("Session released.")

def update_plot(frame, lines, axes):
    """
    Correctly fetches, buffers, copies, and filters data for a stable plot.
    """
    global data_buffer
    try:
        # 1. Get new data from BrainFlow
        num_samples_available = board.get_board_data_count()
        if num_samples_available > 0:
            new_data = board.get_board_data(num_samples_available)
            # 2. Append new raw data to our persistent local buffer
            data_buffer = np.hstack((data_buffer, new_data))
        
        # 3. Keep our local buffer from growing forever
        # Keep more than the window size to provide context for the filter
        buffer_limit = int(window_size * 1.5)
        if data_buffer.shape[1] > buffer_limit:
            data_buffer = data_buffer[:, -buffer_limit:]

        # 4. Create a final, temporary copy of the data SLICE we want to plot
        # This is the key fix: we are now filtering a disposable copy.
        plot_slice = data_buffer[:, -window_size:].copy()
        
        num_plot_points = plot_slice.shape[1]
        
        # Don't try to plot if we have nothing
        if num_plot_points == 0:
            return []

        # 5. Create a time vector that matches the amount of data we have
        time_vector = np.linspace(0, num_plot_points / sampling_rate, num=num_plot_points)
        
        for i, (line, ax) in enumerate(zip(lines, axes)):
            channel_idx = eeg_channels[i]
            
            # 6. Filter the disposable copy. Our main `data_buffer` remains pristine.
            DataFilter.perform_highpass(plot_slice[channel_idx], sampling_rate, 0.5, 4, FilterTypes.BUTTERWORTH.value, 0)

            plot_data_uV = plot_slice[channel_idx] * 1e6

            line.set_data(time_vector, plot_data_uV)
            
            # 7. Rescale the X-axis to make the data "fill up" initially
            ax.set_xlim(0, SECONDS_TO_DISPLAY)

            # Adaptive Y-Axis
            max_abs_val = np.max(np.abs(plot_data_uV))
            if max_abs_val < 50: max_abs_val = 50
            target_ylim = max_abs_val * 1.2
            current_min, current_max = ax.get_ylim()
            if target_ylim > current_max or target_ylim < current_max * 0.5:
                 ax.set_ylim(-target_ylim, target_ylim)

    except Exception as e:
        print(f"!!! ERROR IN UPDATE_PLOT: {e}")

    return lines

if __name__ == "__main__":
    main()
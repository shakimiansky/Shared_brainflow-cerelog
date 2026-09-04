import time
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from brainflow.board_shim import BoardShim, BrainFlowInputParams, BoardIds, BrainFlowError
from brainflow.data_filter import DataFilter, FilterTypes
from brainflow.data_filter import NoiseTypes, DetrendOperations, AggOperations, WaveletTypes, NoiseEstimationLevelTypes, WaveletExtensionTypes, ThresholdTypes, WaveletDenoisingTypes
# --- Configuration ---
BOARD_ID = BoardIds.CERELOG_X8_BOARD
SECONDS_TO_DISPLAY = 10
UPDATE_INTERVAL_MS = 40

# Extra seconds of data kept to the LEFT of the visible window, used only to let the IIR
# filters settle. The 4th-order 0.5 Hz highpass has a time constant of ~0.32s, so 2s is about
# 6 time constants - by the time the signal enters the visible window the startup transient is
# gone. Without this the filters restart from zero on every frame and the left edge writhes.
FILTER_RUNIN_SECONDS = 2.0

# Autoscale: expand instantly so the trace never clips, shrink smoothly so it does not twitch.
Y_AXIS_PADDING_FACTOR = 1.25
Y_SHRINK_RATE = 0.15        # per frame, ~1s to settle at 25fps
Y_SNAP_RATIO = 0.5          # if the range is more than 2x too wide, jump instead of easing
Y_MIN_HALF_RANGE_UV = 5.0   # never zoom in past +/-5uV, a flat channel would just show noise
Y_ROBUST_PERCENTILE = 99.9  # ignore the top/bottom 0.1%: rejects single-sample glitches but
                            # still keeps real features like eye blinks inside the window

# --- Global variables ---
board = None
eeg_channels = []
sampling_rate = 0
window_size = 0
runin_size = 0
data_buffer = np.array([])
y_half_ranges = {}
y_seeded = False

def main():
    """
    Connects to the Cerelog board and creates a robust, real-time, scrolling plot
    with stable filtering and adaptive scaling.
    """
    global board, eeg_channels, sampling_rate, window_size, runin_size, data_buffer, y_half_ranges

    params = BrainFlowInputParams()
    params.timeout = 15
    board = BoardShim(BOARD_ID, params)

    try:
        eeg_channels = BoardShim.get_eeg_channels(BOARD_ID)
        sampling_rate = BoardShim.get_sampling_rate(BOARD_ID)
        window_size = SECONDS_TO_DISPLAY * sampling_rate
        runin_size = int(FILTER_RUNIN_SECONDS * sampling_rate)

        if sampling_rate <= 0:
            raise BrainFlowError("Could not get a valid sampling rate from the board.", 0)

        for i in range(len(eeg_channels)):
            y_half_ranges[i] = 100.0

        print(f"Connecting to {board.get_board_descr(BOARD_ID)['name']}...")
        print(f"Detected Sampling Rate: {sampling_rate} Hz")
        board.prepare_session()
        print("\nStarting stream... Close the plot window to stop.")
        board.start_stream(5 * 60 * sampling_rate)
        time.sleep(2)

        num_board_channels = BoardShim.get_num_rows(BOARD_ID)
        data_buffer = np.empty((num_board_channels, 0))

        # --- Plot Setup ---
        plt.rcParams.update({
            'figure.facecolor': '#ffffff',
            'axes.facecolor': '#fbfbfd',
            'axes.edgecolor': '#c8ccd4',
            'axes.labelcolor': '#3c4048',
            'grid.color': '#e2e5ea',
            'xtick.color': '#6b7280',
            'ytick.color': '#6b7280',
            'font.size': 9,
        })

        fig, axes = plt.subplots(4, 2, figsize=(18, 10), sharex=True)
        fig.suptitle('Real-Time Cerelog EEG Waveforms', fontsize=15, color='#20242b')
        axes_flat = axes.flatten()

        palette = plt.get_cmap('tab10')
        lines = [ax.plot([], [], lw=0.9, color=palette(i % 10), solid_joinstyle='round')[0]
                 for i, ax in enumerate(axes_flat)]

        for i, ax in enumerate(axes_flat):
            ax.set_title(f'Channel {eeg_channels[i]}', fontsize=10, color='#3c4048', pad=4)
            ax.set_ylabel('µV')
            ax.grid(True, linewidth=0.6, alpha=0.9)
            ax.set_axisbelow(True)
            ax.set_xlim(-SECONDS_TO_DISPLAY, 0)
            ax.axhline(0, color='#c8ccd4', lw=0.6, zorder=1)
            for side in ('top', 'right'):
                ax.spines[side].set_visible(False)

        fig.text(0.5, 0.035, 'Time (Seconds from "Now")', ha='center', va='center', color='#3c4048')
        plt.tight_layout(rect=[0, 0.05, 1, 0.96])

        def on_close(event):
            print("Plot window closed, stopping stream...")
            if board and board.is_prepared():
                board.stop_stream()
                board.release_session()
            print("Session released. Exiting.")

        fig.canvas.mpl_connect('close_event', on_close)

        ani = FuncAnimation(fig, update_plot, fargs=(lines, axes_flat),
                            interval=UPDATE_INTERVAL_MS, blit=False, cache_frame_data=False)
        plt.show()

    except Exception as e:
        print(f"An error occurred in main(): {e}")
    finally:
        if board and board.is_prepared():
            board.release_session()

def update_plot(frame, lines, axes):
    """
    This function is called periodically to update the plot data.
    """
    global data_buffer, y_half_ranges, y_seeded

    try:
        new_data = board.get_board_data()
        if new_data.shape[1] > 0:
            data_buffer = np.hstack((data_buffer, new_data))
        # Keep the visible window plus the filter run-in margin.
        buffer_limit = window_size + runin_size
        if data_buffer.shape[1] > buffer_limit:
            data_buffer = data_buffer[:, -buffer_limit:]

        total_points = data_buffer.shape[1]
        if total_points < 32:
            return

        # Filter the run-in margin together with the visible window, then throw the margin away.
        # Every displayed sample has then seen the same amount of filter history regardless of
        # where the window happens to start, so its value stops changing from frame to frame.
        # While the buffer is still filling we trim a proportionally smaller run-in so something
        # shows up right away instead of waiting the full margin.
        runin = min(runin_size, total_points // 4)
        num_points = total_points - runin
        if num_points < 2:
            return

        eeg_plot_data = data_buffer[eeg_channels] * 1e6

        # --- Filtering Logic (Corrected for Real-Time Stability) ---
        for i in range(len(eeg_channels)):
            # Use a safe data length check for the filters
            if eeg_plot_data[i].size > 20:
                #1 Detrend to get dc offset away
                DataFilter.detrend(eeg_plot_data[i], DetrendOperations.CONSTANT.value)
                # 2. Apply a STABLE 4nd-order low-pass 100hz. This is crucial for real-time processing.
                DataFilter.perform_lowpass(eeg_plot_data[i], sampling_rate, 100.0, 4, FilterTypes.BUTTERWORTH, 0)

                # 3. Apply the band-stop (notch) filter for 50, 60 Hz noise.
                DataFilter.perform_bandstop(eeg_plot_data[i], sampling_rate, 48, 52, 3, FilterTypes.BUTTERWORTH, 0)
                DataFilter.perform_bandstop(eeg_plot_data[i], sampling_rate, 58, 62, 3, FilterTypes.BUTTERWORTH, 0)

                #4 High Pass above 0.5 Hz
                DataFilter.perform_highpass(eeg_plot_data[i], sampling_rate, 0.5, 4, FilterTypes.BUTTERWORTH, 0)

        # Drop the run-in margin now that the filters have settled across it.
        eeg_plot_data = eeg_plot_data[:, runin:]

        # --- Manual Time Axis Generation (for True Scrolling) ---
        time_vector_full_window = np.linspace(-SECONDS_TO_DISPLAY, 0, window_size)
        time_vector_for_plot = time_vector_full_window[-num_points:]

        for i, (line, ax) in enumerate(zip(lines, axes)):
            channel_data = eeg_plot_data[i]

            # Check for invalid filter output (NaN) to prevent crashes
            if np.isnan(channel_data).any():
                print(f"Warning: NaN detected in channel {eeg_channels[i]} after filtering. Skipping one update.")
                continue

            # The highpass already removed DC; the median is a stable re-centring that a rare
            # artifact cannot drag around the way a mean can.
            centered_data = channel_data - np.median(channel_data)

            line.set_data(time_vector_for_plot, centered_data)

            # --- Adaptive Y-Axis Logic ---
            # Scale off a robust percentile of the whole visible window rather than the raw
            # min/max, so one spike does not zoom the whole trace out.
            hi = np.percentile(centered_data, Y_ROBUST_PERCENTILE)
            lo = np.percentile(centered_data, 100.0 - Y_ROBUST_PERCENTILE)
            target_half = max(abs(hi), abs(lo)) * Y_AXIS_PADDING_FACTOR
            target_half = max(target_half, Y_MIN_HALF_RANGE_UV)

            current_half = y_half_ranges[i]
            if (not y_seeded) or (target_half > current_half) or (target_half < current_half * Y_SNAP_RATIO):
                # First frame, about to clip, or wildly over-zoomed: go there now.
                new_half = target_half
            else:
                # Settle in gently.
                new_half = current_half * (1 - Y_SHRINK_RATE) + target_half * Y_SHRINK_RATE

            y_half_ranges[i] = new_half
            ax.set_ylim(-new_half, new_half)

        y_seeded = True

    except Exception as e:
        print(f"!!! ERROR IN UPDATE_PLOT: {e}")

if __name__ == "__main__":
    main()

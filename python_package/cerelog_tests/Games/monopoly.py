#!/usr/bin/env python3
"""
SSVEP Continuous Game using BrainFlow + PsychoPy.

This version is completely restructured to be a real-time, continuous game,
using the same continuous data polling architecture as the user's working plotter.
This prevents resource starvation and allows for ongoing gameplay.

UPDATES:
- Analysis restricted to the first 4 EEG channels.
- UI now displays the command letter ('A', 'B', 'C') on each flicker target.
"""

import argparse
import time
import enum 
from dataclasses import dataclass
from typing import List, Optional

import numpy as np

# BrainFlow
from brainflow.board_shim import BoardShim, BrainFlowInputParams, BoardIds
from brainflow.data_filter import DataFilter, FilterTypes

# PsychoPy
from psychopy import visual, core, event

# SciPy for analysis
from scipy.signal import welch



class DetrendOperations(enum.IntEnum):
    """Enum to store all supported detrend options"""

    NO_DETREND = 0  #:
    CONSTANT = 1  #:
    LINEAR = 2  #:

# --- HARDCODED BOARD CONFIGURATION ---
BOARD_ID = BoardIds.CERELOG_X8_BOARD  # Corrected board ID

# --- GAME CONFIGURATION ---
ANALYSIS_INTERVAL_SEC = 4.0  # How often to check for a new command (in seconds)
ANALYSIS_WINDOW_SEC = 3.0    # How many seconds of data to use for each analysis


# =======================
# Stimulus / Flicker UI
# =======================
@dataclass
class FlickerTarget:
    label: str
    freq_hz: float
    rect: visual.Rect
    command_label: visual.TextStim 
    phase: float = 0.0
    duty: float = 0.5

    def update_visibility(self, t_s: float):
        frac = (self.phase + self.freq_hz * t_s) % 1.0
        is_on = frac < self.duty
        self.rect.opacity = 1.0 if is_on else 0.0
        self.command_label.opacity = 1.0
        self.command_label.color = 'black' if is_on else 'white'


def build_targets(win, freqs: List[float]) -> List[FlickerTarget]:
    n = len(freqs)
    box_size = 0.25
    offset = 0.65
    corner_positions = [(-offset, offset), (offset, offset), (-offset, -offset), (offset, -offset)]
    
    positions = corner_positions[:n]
    targets = []
    corner_labels = ["Action A", "Action B", "Action C", "Action D"]
    command_letters = ["A", "B", "C", "D"]
    
    for i, f in enumerate(freqs):
        pos = positions[i]
        rect = visual.Rect(win, width=box_size, height=box_size, fillColor='white', 
                           lineColor='white', pos=pos, opacity=0.0)
        
        desc_lbl = visual.TextStim(win, text=f"{corner_labels[i]}\n({f:.1f} Hz)",
                                   pos=(pos[0], pos[1] - 0.15), height=0.05, color='white', alignText='center')
        desc_lbl.draw()
        
        cmd_lbl = visual.TextStim(win, text=command_letters[i], pos=pos, height=0.15, bold=True)
        
        target = FlickerTarget(
            label=f"{f:.1f} Hz ({corner_labels[i]})", 
            freq_hz=f, 
            rect=rect,
            command_label=cmd_lbl
        )
        targets.append(target)
    
    fixation = visual.TextStim(win, text='+', height=0.1, color='gray', pos=(0, 0))
    fixation.draw()
    return targets


# =======================
# Real-Time Analysis
# =======================
def analyze_data_chunk(data_chunk: np.ndarray, sr: float, freqs: List[float]) -> Optional[str]:
    """
    Analyzes a chunk of recent EEG data to find the dominant SSVEP frequency.
    """
    if not data_chunk.any() or data_chunk.shape[1] < sr:
        return None

    all_eeg_channels = BoardShim.get_eeg_channels(BOARD_ID)
    eeg_channels_to_use = all_eeg_channels[:4]
    print(f"Analyzing data using channels: {eeg_channels_to_use}")

    eeg_data = data_chunk[eeg_channels_to_use, :]
    
    for i in range(eeg_data.shape[0]):
        
        #1 Detrend to get dc offset away
        DataFilter.detrend(eg_data[i], DetrendOperations.CONSTANT.value)
        # 2. Apply a STABLE 4nd-order low-pass 100hz. This is crucial for real-time processing.
        DataFilter.perform_lowpass(eg_data[i], sampling_rate, 100.0, 4, FilterTypes.BUTTERWORTH, 0)
        
        # 3. Apply the band-stop (notch) filter for 50, 60 Hz noise.
        DataFilter.perform_bandstop(eg_data[i], sampling_rate, 48, 52, 3, FilterTypes.BUTTERWORTH, 0)
        DataFilter.perform_bandstop(eeg_data[i], sampling_rate, 58, 62, 3, FilterTypes.BUTTERWORTH, 0)
        
        #4 High Pass above 0.5 Hz
        DataFilter.perform_highpass(eg_data[i], sampling_rate, 0.5, 4, FilterTypes.BUTTERWORTH, 0)
        
        #5. More cleaning data up
        #DataFilter.perform_rolling_filter(eeg_plot_data[i], 3, AggOperations.MEAN.value)
        DataFilter.perform_rolling_filter(eeg_data[i], 3, AggOperations.MEDIAN.value)





    mean_eeg = np.mean(eeg_data, axis=0)
    
    nperseg = min(len(mean_eeg), int(2*sr))
    f, pxx = welch(mean_eeg, fs=sr, nperseg=nperseg)
    
    powers = {}
    for target_freq in freqs:
        freq_idx = np.argmin(np.abs(f - target_freq))
        powers[target_freq] = pxx[freq_idx]

    if not powers:
        return None
        
    detected_freq = max(powers, key=powers.get)
    
    # Associate the detected frequency back to a command letter
    for i, freq in enumerate(freqs):
        if detected_freq == freq:
            return chr(ord('a') + i) # 'a' for first freq, 'b' for second, etc.
    
    return None


# =======================
# Main Game Loop
# =======================
def main():
    parser = argparse.ArgumentParser(description=f"SSVEP Continuous Game for Cerelog X8")
    parser.add_argument("--freqs", type=float, nargs="+", default=[7.0, 9.5, 12.0], help="Target flicker frequencies (Hz)")
    args = parser.parse_args()

    board = None
    try:
        # --- Board Setup ---
        params = BrainFlowInputParams()
        params.timeout = 15
        board = BoardShim(BOARD_ID, params)
        board.prepare_session()
        sampling_rate = BoardShim.get_sampling_rate(BOARD_ID)
        
        print(f"Board connected. SR: {sampling_rate} Hz. Starting stream...")
        board.start_stream(450000)
        time.sleep(4)

        # --- PsychoPy Setup ---
        win = visual.Window(size=(1470, 800), fullscr=False, color='black', units='norm')
        targets = build_targets(win, args.freqs)
        
        info_text = visual.TextStim(win, text="Focus on a command...", pos=(0, 0.6), height=0.05, color='white')
        command_text = visual.TextStim(win, text="Last Command: NONE", pos=(0, -0.6), height=0.07, color='white')
        
        # --- Game State Variables ---
        game_clock = core.Clock()
        last_analysis_time = 0
        
        num_board_channels = BoardShim.get_num_rows(BOARD_ID)
        data_buffer = np.empty((num_board_channels, 0))
        
        # --- THE GAME LOOP ---
        while not event.getKeys(keyList=['escape']):
            
            new_data = board.get_board_data()
            if new_data.shape[1] > 0:
                data_buffer = np.hstack((data_buffer, new_data))

            buffer_limit_samples = int(20 * sampling_rate)
            if data_buffer.shape[1] > buffer_limit_samples:
                data_buffer = data_buffer[:, -buffer_limit_samples:]

            current_time = game_clock.getTime()
            for target in targets:
                target.update_visibility(current_time)
            
            if current_time - last_analysis_time >= ANALYSIS_INTERVAL_SEC:
                last_analysis_time = current_time
                print(f"\n--- Running analysis at {current_time:.1f}s ---")

                samples_for_analysis = int(ANALYSIS_WINDOW_SEC * sampling_rate)
                
                if data_buffer.shape[1] >= samples_for_analysis:
                    analysis_chunk = data_buffer[:, -samples_for_analysis:]
                    command = analyze_data_chunk(analysis_chunk, sampling_rate, args.freqs)

                    if command:
                        print(f"*** COMMAND DETECTED: {command.upper()} ***")
                        command_text.text = f"Last Command: {command.upper()}"
                else:
                    print(f"Not enough data to analyze yet. Have {data_buffer.shape[1]}/{samples_for_analysis} samples.")

            # --- Drawing Loop ---
            for target in targets:
                target.rect.draw()
                target.command_label.draw()
                
            info_text.draw()
            command_text.draw()
            win.flip()
            
    # --- THIS IS THE LINE THAT WAS FIXED ---
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        if board and board.is_prepared():
            print("Stopping stream and releasing session.")
            board.stop_stream()
            board.release_session()
        core.quit()

if __name__ == "__main__":
    main()
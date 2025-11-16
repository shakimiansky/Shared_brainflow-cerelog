#!/usr/bin/env python3
"""
SSVEP Continuous Game using BrainFlow + PsychoPy.

This version is completely restructured to be a real-time, continuous game,
using the same continuous data polling architecture as the user's working plotter.
This prevents resource starvation and allows for ongoing gameplay.
"""

import argparse
import time
from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import pandas as pd
from scipy.signal import welch

# BrainFlow
from brainflow.board_shim import BoardShim, BrainFlowInputParams, BoardIds
from brainflow.data_filter import DataFilter, FilterTypes

# PsychoPy
from psychopy import visual, core, event, monitors

# --- HARDCODED BOARD CONFIGURATION ---
BOARD_ID = BoardIds.CERELOG_X8_BOARD

# --- GAME CONFIGURATION ---
ANALYSIS_INTERVAL_SEC = 4.0  # How often to check for a new command (in seconds)
ANALYSIS_WINDOW_SEC = 3.0    # How many seconds of data to use for each analysis


# =======================
# Stimulus / Flicker UI (Unchanged)
# =======================
@dataclass
class FlickerTarget:
    label: str
    freq_hz: float
    rect: visual.Rect
    phase: float = 0.0
    duty: float = 0.5

    def update_visibility(self, t_s: float):
        frac = (self.phase + self.freq_hz * t_s) % 1.0
        self.rect.opacity = 1.0 if frac < self.duty else 0.0


def build_targets(win, freqs: List[float]) -> List[FlickerTarget]:
    n = len(freqs)
    box_size = 0.25
    offset = 0.65
    corner_positions = [(-offset, offset), (offset, offset), (-offset, -offset), (offset, -offset)]
    
    positions = corner_positions[:n]
    targets = []
    corner_labels = ["Action A", "Action B", "Action C", "Action D"]
    
    for i, f in enumerate(freqs):
        pos = positions[i]
        rect = visual.Rect(win, width=box_size, height=box_size, fillColor='white', 
                           lineColor='white', pos=pos, opacity=0.0)
        lbl = visual.TextStim(win, text=f"{corner_labels[i]}\n({f:.1f} Hz)",
                              pos=(pos[0], pos[1] - 0.15), height=0.05, color='white', alignText='center')
        targets.append(FlickerTarget(label=f"{f:.1f} Hz ({corner_labels[i]})", freq_hz=f, rect=rect))
        lbl.draw()
    
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
    if not data_chunk.any() or data_chunk.shape[1] < sr: # Need at least 1s of data
        return None

    eeg_channels = BoardShim.get_eeg_channels(BOARD_ID)
    eeg_data = data_chunk[eeg_channels, :]
    
    # --- Filter each channel ---
    for i in range(eeg_data.shape[0]):
        DataFilter.perform_lowpass(eeg_data[i], int(sr), 100.0, 2, FilterTypes.BUTTERWORTH, 0)
        DataFilter.perform_bandstop(eeg_data[i], int(sr), 60.0, 4.0, 3, FilterTypes.BUTTERWORTH, 0)

    # --- PSD Analysis ---
    mean_eeg = np.mean(eeg_data, axis=0)
    f, pxx = welch(mean_eeg, fs=sr, nperseg=int(2*sr))
    
    powers = {}
    for target_freq in freqs:
        freq_idx = np.argmin(np.abs(f - target_freq))
        powers[target_freq] = pxx[freq_idx]

    detected_freq = max(powers, key=powers.get)
    
    if detected_freq == freqs[0]: return "a"
    if detected_freq == freqs[1]: return "b"
    if detected_freq == freqs[2]: return "c"
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
        time.sleep(4) # Wait for the stream to stabilize

        # --- PsychoPy Setup ---
        win = visual.Window(size=(1470, 800), fullscr=False, color='black', units='norm')
        targets = build_targets(win, args.freqs)
        
        info_text = visual.TextStim(win, text="Focus on a command...", pos=(0, 0.6), height=0.05)
        command_text = visual.TextStim(win, text="Last Command: NONE", pos=(0, -0.6), height=0.07)
        
        # --- Game State Variables ---
        game_clock = core.Clock()
        last_analysis_time = 0
        data_buffer = np.empty((BoardShim.get_num_rows(BOARD_ID), 0))
        
        # --- THE GAME LOOP ---
        while not event.getKeys(keyList=['escape']):
            
            # 1. Pull Data (like the plotter)
            new_data = board.get_board_data()
            if new_data.shape[1] > 0:
                data_buffer = np.hstack((data_buffer, new_data))

            # Keep the buffer from growing forever (keep last ~20s)
            buffer_limit = int(20 * sampling_rate)
            if data_buffer.shape[1] > buffer_limit:
                data_buffer = data_buffer[:, -buffer_limit:]

            # 2. Update Stimulus
            current_time = game_clock.getTime()
            for target in targets:
                target.update_visibility(current_time)
            
            # 3. Check if it's time to analyze
            if current_time - last_analysis_time > ANALYSIS_INTERVAL_SEC:
                last_analysis_time = current_time
                print(f"\n--- Running analysis at {current_time:.1f}s ---")

                # Get the most recent chunk of data for analysis
                samples_for_analysis = int(ANALYSIS_WINDOW_SEC * sampling_rate)
                if data_buffer.shape[1] > samples_for_analysis:
                    analysis_chunk = data_buffer[:, -samples_for_analysis:]
                    
                    # Run the analysis
                    command = analyze_data_chunk(analysis_chunk, sampling_rate, args.freqs)

                    if command:
                        print(f"*** COMMAND DETECTED: {command.upper()} ***")
                        command_text.text = f"Last Command: {command.upper()}"
                        #
                        # THIS IS WHERE YOU WOULD TRIGGER A GAME ACTION
                        # e.g., if command == 'a': player.move_left()
                        #
                else:
                    print("Not enough data in buffer to run analysis yet.")

            # 4. Draw Everything
            for target in targets:
                target.rect.draw()
            info_text.draw()
            command_text.draw()
            win.flip()

    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        # --- Cleanup ---
        if board and board.is_prepared():
            print("Stopping stream and releasing session.")
            board.stop_stream()
            board.release_session()
        core.quit()

if __name__ == "__main__":
    main()
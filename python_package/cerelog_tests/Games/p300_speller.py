#!/usr/bin/env python3
"""
P300 Speller Demonstration using BrainFlow and PsychoPy.

This application presents a 6x6 grid of characters. The user focuses on a desired
character while rows and columns flash randomly. The program records EEG data and
uses the P300 brainwave (an Event-Related Potential) to determine which
character the user was looking at.
"""

import argparse
import time
import random
import numpy as np
from typing import List, Dict, Optional, Tuple

# BrainFlow
from brainflow.board_shim import BoardShim, BrainFlowInputParams, BoardIds, BrainFlowError
from brainflow.data_filter import DataFilter, FilterTypes, DetrendOperations

# PsychoPy for visual presentation
from psychopy import visual, core, event

# --- BCI CONFIGURATION ---
BOARD_ID = BoardIds.CERELOG_X8_BOARD

# P300 specific parameters
FLASH_DURATION_S = 0.100  # How long each row/col is highlighted (in seconds)
INTER_FLASH_INTERVAL_S = 0.150  # Time between flashes
N_REPEATS = 8             # How many times to flash each row and column in a single trial
P300_WINDOW_START_S = 0.250 # Start of the time window to search for a P300 peak (post-stimulus)
P300_WINDOW_END_S = 0.500   # End of the time window

# --- UI CONFIGURATION ---
CHAR_GRID = [
    ['A', 'B', 'C', 'D', 'E', 'F'],
    ['G', 'H', 'I', 'J', 'K', 'L'],
    ['M', 'N', 'O', 'P', 'Q', 'R'],
    ['S', 'T', 'U', 'V', 'W', 'X'],
    ['Y', 'Z', '1', '2', '3', '4'],
    ['5', '6', '7', '8', '9', '_']
]
GRID_SIZE = 6

# =======================
# UI Helper Class
# =======================
class CharacterCell:
    """Helper class to manage each character's visual element."""
    def __init__(self, win, char, pos, size):
        self.char = char
        self.is_highlighted = False
        self.text = visual.TextStim(win, text=char, pos=pos, height=size * 0.75, color='white')

    def draw(self):
        self.text.color = 'cyan' if self.is_highlighted else 'white'
        self.text.draw()

# =======================
# P300 Analysis Function
# =======================
def analyze_p300_trial(trial_data: np.ndarray, sr: float) -> Optional[str]:
    """
    Processes EEG data from a P300 trial to detect the target character.
    This is the core of the BCI.
    """
    eeg_channels = BoardShim.get_eeg_channels(BOARD_ID)
    marker_channel = BoardShim.get_marker_channel(BOARD_ID)
    timestamp_channel = BoardShim.get_timestamp_channel(BOARD_ID)
    
    # Use only the first 4 EEG channels for analysis as requested
    eeg_channels_to_use = eeg_channels[:4]
    
    # 1. Filter the EEG data
    for chan in eeg_channels_to_use:
        # P300 is a low-frequency component, so a bandpass filter is effective.

        DataFilter.perform_bandstop(trial_data[chan], int(sr), 58, 62, 3, FilterTypes.BUTTERWORTH, 0)

        
        DataFilter.perform_bandpass(trial_data[chan], sr, 1.0, 10.0, 4, FilterTypes.BUTTERWORTH, 0)
        DataFilter.detrend(trial_data[chan], DetrendOperations.CONSTANT.value)

    # 2. Find markers (where flashes occurred)
    marker_indices = np.where(trial_data[marker_channel] > 0)[0]
    markers = trial_data[marker_channel, marker_indices].astype(int)

    if len(marker_indices) != GRID_SIZE * 2 * N_REPEATS:
        print(f"Warning: Expected {GRID_SIZE * 2 * N_REPEATS} markers, found {len(marker_indices)}. Analysis may be unreliable.")
        return None
        
    # 3. Create epochs (small time windows of EEG after each flash)
    epoch_start_offset = int(0.0 * sr) # Start epoch at the time of the flash
    epoch_end_offset = int(0.8 * sr)   # End epoch 800ms after the flash
    
    avg_erps: Dict[int, np.ndarray] = {} # Dict to store the average ERP for each marker type
    
    for marker_type in range(1, (GRID_SIZE * 2) + 1): # Markers are 1-12
        # Find all timestamps where this specific marker appeared
        indices_for_marker = marker_indices[np.where(markers == marker_type)]
        
        if len(indices_for_marker) == 0:
            continue
            
        # Create an array to hold all epochs for this marker type
        epochs = np.zeros((len(indices_for_marker), len(eeg_channels_to_use), epoch_end_offset - epoch_start_offset))
        
        for i, idx in enumerate(indices_for_marker):
            start_sample = idx + epoch_start_offset
            end_sample = idx + epoch_end_offset
            epoch = trial_data[eeg_channels_to_use, start_sample:end_sample]
            
            # Baseline correction (subtract the mean of the first 100ms)
            baseline = np.mean(epoch[:, :int(0.1 * sr)], axis=1, keepdims=True)
            epochs[i, :, :] = epoch - baseline

        # Average all the epochs for this marker to get the ERP
        avg_erps[marker_type] = np.mean(epochs, axis=0)
    
    # 4. Find the row and column with the strongest P300 response
    p300_start_idx = int(P300_WINDOW_START_S * sr)
    p300_end_idx = int(P300_WINDOW_END_S * sr)

    max_amplitudes = {}
    for marker_type, erp in avg_erps.items():
        # We look at the mean ERP across our chosen channels
        mean_erp_across_chans = np.mean(erp, axis=0)
        # Find the maximum amplitude within the P300 window
        max_amplitudes[marker_type] = np.max(mean_erp_across_chans[p300_start_idx:p300_end_idx])

    # Markers 1-6 are for rows, 7-12 are for columns
    target_row_marker = max(max_amplitudes.keys() & range(1, 7), key=max_amplitudes.get)
    target_col_marker = max(max_amplitudes.keys() & range(7, 13), key=max_amplitudes.get)

    # Convert marker back to 0-based index
    target_row = target_row_marker - 1
    target_col = target_col_marker - 7

    return CHAR_GRID[target_row][target_col]

# =======================
# Main Speller Logic
# =======================
def main():
    board = None
    try:
        # --- Board Setup ---
        params = BrainFlowInputParams()
        params.timeout = 15
        board = BoardShim(BOARD_ID, params)
        board.prepare_session()
        sampling_rate = BoardShim.get_sampling_rate(BOARD_ID)
        
        # --- PsychoPy Window and UI Setup ---
        win = visual.Window(size=(1000, 800), fullscr=False, color='black', units='norm')
        
        info_text = visual.TextStim(win, pos=(0, 0.9), height=0.06)
        spelled_text_stim = visual.TextStim(win, text="Spelled: ", pos=(0, -0.9), height=0.08)

        # Create the grid of character cells
        cells = []
        cell_size = 0.2
        start_pos = -0.5
        for r in range(GRID_SIZE):
            row_cells = []
            for c in range(GRID_SIZE):
                pos = (start_pos + c * cell_size, start_pos + (GRID_SIZE - 1 - r) * cell_size)
                cell = CharacterCell(win, CHAR_GRID[r][c], pos, cell_size)
                row_cells.append(cell)
            cells.append(row_cells)

        # --- Main Program Loop (State Machine) ---
        spelled_word = ""
        is_running = True
        while is_running:
            # STATE 1: FOCUS / PREPARE
            info_text.text = "Prepare for next character. Focus on your target."
            spelled_text_stim.text = f"Spelled: {spelled_word}"
            for r in cells:
                for c in r: c.draw()
            info_text.draw()
            spelled_text_stim.draw()
            win.flip()
            core.wait(4.0)

            # STATE 2: FLASHING SEQUENCE
            # Start stream and clear board buffer just before the trial begins
            board.start_stream(45000)
            time.sleep(1) # wait for stream to stabilize
            board.get_board_data() 
            
            info_text.text = "Keep focusing..."
            
            # Generate the random flash sequence (rows 1-6, cols 7-12)
            flash_sequence = []
            for i in range(N_REPEATS):
                # Add all 6 rows and 6 columns to the sequence
                flash_sequence.extend(random.sample(range(1, 13), 12))

            # Run the flashing sequence
            for flash_num in flash_sequence:
                is_row_flash = flash_num <= 6
                idx = flash_num - 1 if is_row_flash else flash_num - 7

                # Highlight the row/column
                for i in range(GRID_SIZE):
                    cell = cells[idx][i] if is_row_flash else cells[i][idx]
                    cell.is_highlighted = True
                
                # Draw the highlighted grid, then insert marker and flip
                spelled_text_stim.draw()
                info_text.draw()
                for r in cells:
                    for c in r: c.draw()
                win.callOnFlip(board.insert_marker, flash_num)
                win.flip()
                core.wait(FLASH_DURATION_S)

                # Un-highlight
                for i in range(GRID_SIZE):
                    cell = cells[idx][i] if is_row_flash else cells[i][idx]
                    cell.is_highlighted = False
                
                # Draw the grid in its normal state
                spelled_text_stim.draw()
                info_text.draw()
                for r in cells:
                    for c in r: c.draw()
                win.flip()
                core.wait(INTER_FLASH_INTERVAL_S)

            # Give a bit of extra time for the last ERP to be recorded
            time.sleep(1.0) 
            
            # STATE 3: ANALYZE DATA
            trial_data = board.get_board_data()
            board.stop_stream()
            
            info_text.text = "Analyzing..."
            spelled_text_stim.draw()
            info_text.draw()
            for r in cells:
                for c in r: c.draw()
            win.flip()

            detected_char = analyze_p300_trial(trial_data, sampling_rate)
            
            # STATE 4: SHOW RESULT
            if detected_char:
                spelled_word += detected_char
            
            if event.getKeys(keyList=['escape']):
                is_running = False

    except BrainFlowError as e:
        print(f"BrainFlow Error: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
    finally:
        if board and board.is_prepared():
            print("Releasing session...")
            board.release_session()
        win.close()
        core.quit()

if __name__ == "__main__":
    main()
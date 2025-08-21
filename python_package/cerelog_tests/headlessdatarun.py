# test_headless.py
import time
from brainflow.board_shim import BoardShim, BrainFlowInputParams, BoardIds

BOARD_ID = BoardIds.CERELOG_X8_BOARD
DURATION_SECONDS = 30

def main():
    params = BrainFlowInputParams()
    board = BoardShim(BOARD_ID, params)
    
    try:
        print("Preparing session...")
        board.prepare_session()
        
        print(f"Starting stream for {DURATION_SECONDS} seconds...")
        board.start_stream()
        
        packet_count = 0
        last_timestamp = 0
        start_time = time.time()
        
        while time.time() - start_time < DURATION_SECONDS:
            time.sleep(0.5) # Check for data every 500ms
            
            num_samples = board.get_board_data_count()
            if num_samples > 0:
                data = board.get_board_data(num_samples)
                
                # Check for timestamp gaps
                current_timestamp = data[BoardShim.get_timestamp_channel(BOARD_ID)][-1]
                if last_timestamp > 0:
                    time_diff = current_timestamp - last_timestamp
                    # Expected diff is ~0.5s. A "glitch" would be a large gap.
                    if time_diff > 1.0:
                        print(f"!!! POTENTIAL GLITCH DETECTED: Large time gap of {time_diff:.2f} seconds.")
                
                last_timestamp = current_timestamp
                packet_count += num_samples
                print(f"Received {num_samples} samples. Total: {packet_count}")

    
    finally:
        # The is_prepared() check is the correct way to see if the session is active.
        # stop_stream() can be called safely even if the stream isn't running.
        if board.is_prepared():
            print("Stopping stream...")
            board.stop_stream()
            print("Releasing session...")
            board.release_session()

if __name__ == "__main__":
    main()
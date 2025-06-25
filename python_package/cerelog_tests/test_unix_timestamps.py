#!/usr/bin/env python3
"""
Test Unix timestamp functionality for Cerelog X8
This test shows the difference between incremental and Unix timestamps
"""

import sys
import os
import time
import platform
import re
from datetime import datetime
import logging

# Add the parent directory to the path to import Brainflow
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brainflow.board_shim import BoardShim, BrainFlowInputParams, BoardIds, LogLevels

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('test_unix_timestamps.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

def analyze_timestamp_logs(log_file):
    """Parse log file for timestamp debugging information"""
    
    if not os.path.exists(log_file):
        logger.error(f"❌ Log file '{log_file}' not found")
        return
    
    logger.info(f"📋 Analyzing timestamp logs from: {log_file}")
    logger.info("=" * 60)
    
    timestamp_patterns = [
        r"Packet #(\d+): board_timestamp=([\d.]+), system_time=([\d.]+), diff=([-\d.]+)s",
        r"Using scanned port for (\w+): (.+)",
        r"Found available port: (.+)",
        r"Sample #(\d+).*ch(\d+)=([\d.-]+).*ch(\d+)=([\d.-]+)"  # Timestamp channel data (RegEx)
    ]
    
    found_timestamps = []
    found_ports = []
    found_samples = []
    
    try:
        with open(log_file, 'r') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                
                # Look for timestamp debugging messages
                for pattern in timestamp_patterns:
                    match = re.search(pattern, line)
                    if match:
                        if "board_timestamp" in line:
                            packet_num = int(match.group(1))
                            board_ts = float(match.group(2))
                            system_ts = float(match.group(3))
                            diff = float(match.group(4))
                            
                            found_timestamps.append({
                                'packet': packet_num,
                                'board_ts': board_ts,
                                'system_ts': system_ts,
                                'diff': diff,
                                'line': line_num
                            })
                        elif "scanned port" in line or "Found available port" in line:
                            found_ports.append({
                                'message': line,
                                'line': line_num
                            })
                        elif "Sample #" in line and "ch" in line:
                            found_samples.append({
                                'message': line,
                                'line': line_num
                            })
    
        # Display results
        if found_timestamps:
            logger.info("🕐 TIMESTAMP DEBUGGING INFO:")
            logger.info("-" * 40)
            for ts_info in found_timestamps:
                board_dt = datetime.fromtimestamp(ts_info['board_ts'])
                system_dt = datetime.fromtimestamp(ts_info['system_ts'])
                
                logger.info(f"Packet {ts_info['packet']}:")
                logger.info(f"  Board:    {ts_info['board_ts']:.2f} -> {board_dt.strftime('%H:%M:%S')}")
                logger.info(f"  System:   {ts_info['system_ts']:.2f} -> {system_dt.strftime('%H:%M:%S')}")
                logger.info(f"  Diff:     {ts_info['diff']:.2f}s\n")
        
        if found_ports:
            logger.info("🔌 PORT DETECTION INFO:")
            logger.info("-" * 40)
            for port_info in found_ports:
                logger.info(f"Line {port_info['line']}: {port_info['message']}")
            logger.info("")
        
        if found_samples:
            logger.info("📊 SAMPLE DATA (most recent 3):")
            logger.info("-" * 40)
            for sample_info in found_samples[-3:]:  # Show last 3 instead of first 3
                logger.info(f"Line {sample_info['line']}: {sample_info['message']}")
            logger.info("")
        
        if not found_timestamps and not found_ports and not found_samples:
            logger.error("❌ No timestamp debugging information found in log")
            logger.error("   Make sure to run the test with debug logging enabled")
    
    except Exception as e:
        logger.error(f"❌ Error reading log file: {e}")

def test_unix_timestamps():
    """Test Unix timestamp functionality"""
    params = BrainFlowInputParams()
    # Port scanning will automatically detect the correct port
    
    logger.info(f"🪪 Testing Unix Timestamps on {platform.system()} (will auto-detect port)")
    logger.info("=" * 60)
    
    params.timeout = 5
    time_len = 5  # seconds - shorter test to see timestamps
    log_file = 'test_unix_timestamps.log'
    
    try:
        board = BoardShim(BoardIds.CERELOG_X8_BOARD, params)
        BoardShim.enable_dev_board_logger()
        BoardShim.set_log_level(LogLevels.LEVEL_DEBUG.value)
        BoardShim.set_log_file(log_file)
        
        sample_rate = BoardShim.get_sampling_rate(BoardIds.CERELOG_X8_BOARD)
        eeg_channels = BoardShim.get_eeg_channels(BoardIds.CERELOG_X8_BOARD)
        timestamp_channel = BoardShim.get_timestamp_channel(BoardIds.CERELOG_X8_BOARD)
        
        logger.info(f"📊 Sample rate: {sample_rate} SPS")
        logger.info(f"📊 EEG channels: {eeg_channels}")
        logger.info(f"📊 Timestamp channel: {timestamp_channel}")
        logger.info("")

        board.prepare_session()
        logger.info("✅ Session prepared successfully")
        
        board.start_stream()
        logger.info("✅ Stream started successfully")
        
        # Collect data for specified duration
        logger.info(f"⏱️  Collecting data for {time_len} seconds...")
        time.sleep(time_len)
        
        board.stop_stream()
        logger.info("✅ Stream stopped successfully")
        
        # Get the data
        data = board.get_board_data()
        board.release_session()
        
        logger.info(f"📈 Collected {data.shape[1]} samples")
        logger.info("")
        
        # Analyze timestamps
        if timestamp_channel >= 0 and timestamp_channel < data.shape[0]:
            timestamps = data[timestamp_channel, :]
            
            logger.info("🕐 TIMESTAMP ANALYSIS:")
            logger.info("-" * 40)
            
            # Show first few timestamps
            logger.info("First 5 timestamps:")
            for i in range(min(5, len(timestamps))):
                timestamp = timestamps[i]
                if timestamp > 0:  # Valid timestamp
                    dt = datetime.fromtimestamp(timestamp)
                    logger.info(f"  Sample {i}: {timestamp:.2f} -> {dt.strftime('%Y-%m-%d %H:%M:%S')}")
                else:
                    logger.info(f"  Sample {i}: {timestamp:.2f} (invalid)")
            
            logger.info("")
            
            # Show timestamp progression
            valid_timestamps = [t for t in timestamps if t > 0]
            if len(valid_timestamps) >= 2:
                first_ts = valid_timestamps[0]
                last_ts = valid_timestamps[-1]
                duration = last_ts - first_ts
                
                logger.info(f"📊 Timestamp Statistics:")
                logger.info(f"  First timestamp: {first_ts:.2f} ({datetime.fromtimestamp(first_ts).strftime('%H:%M:%S')})")
                logger.info(f"  Last timestamp:  {last_ts:.2f} ({datetime.fromtimestamp(last_ts).strftime('%H:%M:%S')})")
                logger.info(f"  Duration:        {duration:.2f} seconds")
                logger.info(f"  Expected:        {time_len} seconds")
                logger.info(f"  Difference:      {abs(duration - time_len):.2f} seconds")
                
                # Check if timestamps are incrementing (old) or Unix (new)
                if first_ts > 1700000000:  # If first timestamp is a large Unix timestamp (after 2023)
                    logger.info(f"  ✅ Detected: Unix timestamps (real time)")
                    logger.info(f"  📊 Format: Unix epoch seconds (e.g., {first_ts:.0f} = {datetime.fromtimestamp(first_ts).strftime('%Y-%m-%d %H:%M:%S')})")
                else:
                    logger.info(f"  ⚠️  Detected: Incrementing timestamps (counter)")
                    logger.info(f"  📊 Format: Packet counter (e.g., {first_ts:.0f} = packet #{first_ts:.0f})")
                
                logger.info("")
                logger.info("🔄 COMPARISON:")
                logger.info("-" * 40)
                logger.info("OLD (Incrementing): 0, 1, 2, 3, 4, 5... (packet counter)")
                logger.info("NEW (Unix):        1703123456, 1703123457, 1703123458... (real time)")
                logger.info("")
                if first_ts > 1700000000:
                    logger.info("✅ SUCCESS: Unix timestamps are working!")
                    logger.info("   - Timestamps represent real time")
                    logger.info("   - Easy to correlate with system logs")
                    logger.info("   - No complex synchronization needed")
                else:
                    logger.info("⚠️  Still using old incrementing timestamps")
                    logger.info("   - Check if new firmware is uploaded")
                    logger.info("   - Check if BrainFlow is rebuilt")
            
            logger.info("")
            
            # Show system time comparison
            system_time = time.time()
            logger.info(f"🖥️  System time: {system_time:.2f} ({datetime.fromtimestamp(system_time).strftime('%H:%M:%S')})")
            
            if len(valid_timestamps) > 0:
                latest_board_time = valid_timestamps[-1]
                time_diff = system_time - latest_board_time
                logger.info(f"⏱️  Time difference (system - board): {time_diff:.2f} seconds")
                
                if abs(time_diff) < 10:
                    logger.info("✅ Timestamps are well synchronized!")
                elif abs(time_diff) < 60:
                    logger.info("⚠️  Timestamps are roughly synchronized (within 1 minute)")
                else:
                    logger.info("❌ Timestamps are not well synchronized")
        
        else:
            logger.error("❌ No timestamp channel found in data")
        
        logger.info("")
        logger.info("📋 Analyzing log file for detailed debugging...")
        logger.info("")
        
        # Analyze the log file
        analyze_timestamp_logs(log_file)
        
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        return False
    
    return True

if __name__ == "__main__":
    logger.info("🧪 Cerelog X8 Unix Timestamp Test")
    logger.info("=" * 40)
    logger.info("")
    
    success = test_unix_timestamps()
    
    if success:
        logger.info("✅ Test completed successfully!")
    else:
        logger.error("❌ Test failed!")
        sys.exit(1) 

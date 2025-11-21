import threading
import time
from collections import deque
import numpy as np
from dash.exceptions import PreventUpdate

import plotly.graph_objs as go
from dash import Dash, dcc, html, Output, Input, State

# --- BrainFlow BCI Configuration ---
from brainflow.board_shim import BoardShim, BrainFlowInputParams, BoardIds, BrainFlowError
from brainflow.data_filter import DataFilter, FilterTypes
from brainflow.data_filter import NoiseTypes, DetrendOperations, AggOperations, WaveletTypes, NoiseEstimationLevelTypes, WaveletExtensionTypes, ThresholdTypes, WaveletDenoisingTypes




# Use the same board ID from the plotter script
BOARD_ID = BoardIds.CERELOG_X8_BOARD 
# The sampling rate will be fetched directly from the board

# --- SSVEP BCI Configuration ---
SSVEP_FREQ_LEFT = 12.0
SSVEP_FREQ_RIGHT = 17.0
CONTROL_SMOOTHING_WINDOW = 10
CONTROL_SENSITIVITY = 0.5

# --- Visual Gradient Stimulus Configuration (Unchanged) ---
GRADIENT_RESOLUTION = 100
GRADIENT_HEIGHT_PX = 120
GRADIENT_Y_POS = 300
FLICKER_INTENSITY = 0.8
GRADIENT_COLOR_MIN = 'hsl(210, 15%, 20%)'
GRADIENT_COLOR_MAX = 'hsl(180, 100%, 90%)'

# --- Game Configuration (Unchanged) ---
GAME_INTERVAL_MS = 50
PADDLE_SPEED = 35
AI_PADDLE_SPEED = 8
INITIAL_BALL_SPEED_Y = -4
BALL_SPIN_FACTOR = 0.05
GAME_WIDTH = 800
GAME_HEIGHT = 600
PADDLE_WIDTH = 150
PADDLE_HEIGHT = 20
BALL_RADIUS = 10

# --- Global BrainFlow & BCI Variables ---
board = None
eeg_channels = []
sampling_rate = 0
control_metric_history = deque(maxlen=CONTROL_SMOOTHING_WINDOW)

# --- Pong Game Setup ---
app = Dash(__name__)
app.title = "BrainFlow BCI Pong (Gradient)"

def get_initial_game_state():
    return {'player_x': GAME_WIDTH / 2, 'ai_x': GAME_WIDTH / 2, 'ball_x': GAME_WIDTH / 2, 'ball_y': GAME_HEIGHT / 2, 'ball_vx': 0, 'ball_vy': INITIAL_BALL_SPEED_Y, 'player_score': 0, 'ai_score': 0, 'best_channel': 0}

app.layout = html.Div(style={'backgroundColor': '#111111', 'color': '#DDDDDD', 'textAlign': 'center', 'fontFamily': 'monospace'}, children=[
    html.H1("BrainFlow BCI Pong (Gradient)"),
    html.P(id='instruction-text', children="Look towards the side of the screen you want to move to."),
    dcc.Graph(id='pong-game-graph', config={'staticPlot': True}),
    dcc.Interval(id='game-interval', interval=GAME_INTERVAL_MS, n_intervals=0),
    dcc.Store(id='game-state-store', data=get_initial_game_state()),
    html.Div(id='focus-metric-display')
])

@app.callback(
    Output('pong-game-graph', 'figure'), Output('game-state-store', 'data'),
    Output('focus-metric-display', 'children'), Output('instruction-text', 'children'),
    Input('game-interval', 'n_intervals'), State('game-state-store', 'data')
)
def update_game(n, state):
    if n is None or not board or not board.is_prepared():
        raise PreventUpdate

    # --- BCI LOGIC MODIFIED FOR BRAINFLOW ---
    # 1. Get recent data from the board (e.g., 2 seconds)
    num_samples_to_get = int(sampling_rate * 2)
    data = board.get_current_board_data(num_samples_to_get)

    # Check if we have enough data to process
    if data.shape[1] < sampling_rate:
        fig = go.Figure()
        fig.update_layout(xaxis=dict(range=[0, GAME_WIDTH], visible=False), yaxis=dict(range=[0, GAME_HEIGHT], visible=False), plot_bgcolor='#000', paper_bgcolor='#111', annotations=[dict(text="Waiting for EEG Data...", x=GAME_WIDTH/2, y=GAME_HEIGHT/2, showarrow=False, font=dict(size=24, color='white'))])
        return fig, state, "Status: Waiting for data...", "Connecting to your brain..."

    best_control_metric = 0
    best_channel_idx = -1
    
    # 2. Process each EEG channel
    for channel_num in eeg_channels:
        # BrainFlow returns data in microvolts, which is what the game logic expects.
        eeg_data = data[channel_num]

        # --- APPLY FILTERS FROM PLOTTER SCRIPT ---
        if eeg_data.size > 20:
            
          

            #1 Detrend to get dc offset away
            DataFilter.detrend(eeg_data, DetrendOperations.CONSTANT.value)
            # 2. Apply a STABLE 4nd-order low-pass 100hz. This is crucial for real-time processing.
            DataFilter.perform_lowpass(eeg_data, sampling_rate, 100.0, 4, FilterTypes.BUTTERWORTH, 0)
            
            # 3. Apply the band-stop (notch) filter for 50, 60 Hz noise.
            DataFilter.perform_bandstop(eeg_data, sampling_rate, 48, 52, 3, FilterTypes.BUTTERWORTH, 0)
            DataFilter.perform_bandstop(eeg_data, sampling_rate, 58, 62, 3, FilterTypes.BUTTERWORTH, 0)
            
            #4 High Pass above 0.5 Hz
            DataFilter.perform_highpass(eeg_data, sampling_rate, 0.5, 4, FilterTypes.BUTTERWORTH, 0)
            
            #5. More cleaning data up
            #DataFilter.perform_rolling_filter(eeg_plot_data[i], 3, AggOperations.MEAN.value)
            DataFilter.perform_rolling_filter(eeg_data, 3, AggOperations.MEDIAN.value)

            
            

            
        # 3. Perform FFT and calculate PSD (logic from original pong game)
        y_data = eeg_data - np.mean(eeg_data)
        N = len(y_data)
        win = np.hanning(N)
        y_win = y_data * win
        yf = np.fft.fft(y_win)
        xf = np.fft.fftfreq(N, 1.0 / sampling_rate)[:N//2]
        psd = (2 / (sampling_rate * np.sum(win**2))) * np.abs(yf[0:N//2])**2

        # 4. Find power at target frequencies and calculate control metric
        idx_left = np.argmin(np.abs(xf - SSVEP_FREQ_LEFT))
        idx_right = np.argmin(np.abs(xf - SSVEP_FREQ_RIGHT))
        current_metric = psd[idx_right] - psd[idx_left]

        if abs(current_metric) > abs(best_control_metric):
            best_control_metric = current_metric
            best_channel_idx = channel_num
    
    # --- Game Logic (Unchanged) ---
    state['best_channel'] = best_channel_idx
    control_metric_history.append(best_control_metric)
    smoothed_metric = np.mean(control_metric_history)
    max_abs_val = max(abs(m) for m in control_metric_history) if control_metric_history else 1.0
    control_signal = np.clip(smoothed_metric / (max_abs_val + 1e-9), -1.0, 1.0) * CONTROL_SENSITIVITY
    
    state['player_x'] += PADDLE_SPEED * control_signal
    state['player_x'] = max(PADDLE_WIDTH / 2, min(GAME_WIDTH - PADDLE_WIDTH / 2, state['player_x']))
    if state['ai_x'] < state['ball_x']: state['ai_x'] += AI_PADDLE_SPEED
    if state['ai_x'] > state['ball_x']: state['ai_x'] -= AI_PADDLE_SPEED
    state['ball_x'] += state['ball_vx']
    state['ball_y'] += state['ball_vy']
    if state['ball_x'] <= BALL_RADIUS or state['ball_x'] >= GAME_WIDTH - BALL_RADIUS: state['ball_vx'] *= -1
    if state['ball_vy'] < 0 and state['ball_y'] - BALL_RADIUS < PADDLE_HEIGHT and abs(state['player_x'] - state['ball_x']) < PADDLE_WIDTH / 2:
        state['ball_vy'] *= -1
        state['ball_vx'] += (state['ball_x'] - state['player_x']) * BALL_SPIN_FACTOR
    if state['ball_vy'] > 0 and state['ball_y'] + BALL_RADIUS > GAME_HEIGHT - PADDLE_HEIGHT and abs(state['ai_x'] - state['ball_x']) < PADDLE_WIDTH / 2:
        state['ball_vy'] *= -1
        state['ball_vx'] += (state['ball_x'] - state['ai_x']) * BALL_SPIN_FACTOR
    if state['ball_y'] < -BALL_RADIUS or state['ball_y'] > GAME_HEIGHT + BALL_RADIUS:
        if state['ball_y'] < 0: state['ai_score'] += 1
        else: state['player_score'] += 1
        p_score, a_score, b_chan = state['player_score'], state['ai_score'], state['best_channel']
        state = get_initial_game_state()
        state.update({'player_score': p_score, 'ai_score': a_score, 'best_channel': b_chan})

    # --- Drawing Logic (Unchanged) ---
    fig = go.Figure()
    current_time_s = n * GAME_INTERVAL_MS / 1000.0
    flicker_val_left = 0.5 + (np.sin(2 * np.pi * SSVEP_FREQ_LEFT * current_time_s) * (FLICKER_INTENSITY / 2))
    flicker_val_right = 0.5 + (np.sin(2 * np.pi * SSVEP_FREQ_RIGHT * current_time_s) * (FLICKER_INTENSITY / 2))
    left_weights = np.linspace(1, 0, GRADIENT_RESOLUTION)
    right_weights = 1 - left_weights
    gradient_z_values = (flicker_val_left * left_weights) + (flicker_val_right * right_weights)
    fig.add_trace(go.Heatmap(z=[gradient_z_values], x=np.linspace(0, GAME_WIDTH, GRADIENT_RESOLUTION), y=[GRADIENT_Y_POS - GRADIENT_HEIGHT_PX/2, GRADIENT_Y_POS + GRADIENT_HEIGHT_PX/2], colorscale=[[0, GRADIENT_COLOR_MIN], [1, GRADIENT_COLOR_MAX]], showscale=False, zmin=0, zmax=1))
    fig.add_shape(type="rect", x0=state['player_x']-PADDLE_WIDTH/2, y0=0, x1=state['player_x']+PADDLE_WIDTH/2, y1=PADDLE_HEIGHT, fillcolor="cyan", line=dict(width=0))
    fig.add_shape(type="rect", x0=state['ai_x']-PADDLE_WIDTH/2, y0=GAME_HEIGHT-PADDLE_HEIGHT, x1=state['ai_x']+PADDLE_WIDTH/2, y1=GAME_HEIGHT, fillcolor="magenta", line=dict(width=0))
    fig.add_shape(type="circle", x0=state['ball_x']-BALL_RADIUS, y0=state['ball_y']-BALL_RADIUS, x1=state['ball_x']+BALL_RADIUS, y1=state['ball_y']+BALL_RADIUS, fillcolor="white", line=dict(width=0))
    fig.update_layout(xaxis=dict(range=[0, GAME_WIDTH], visible=False), yaxis=dict(range=[0, GAME_HEIGHT], visible=False), plot_bgcolor='#000', paper_bgcolor='#111', margin=dict(l=10, r=10, t=10, b=10),
                      annotations=[dict(text=str(state['player_score']), x=30, y=GAME_HEIGHT/2 - 30, showarrow=False, font=dict(size=40, color='cyan')),
                                   dict(text=str(state['ai_score']), x=30, y=GAME_HEIGHT/2 + 30, showarrow=False, font=dict(size=40, color='magenta'))])
    
    focus_text = f"Best Channel: {state.get('best_channel', 'N/A')} | Control Signal: {control_signal:.2f}"
    instruction_text = f"Look Left ({SSVEP_FREQ_LEFT}Hz) to Move Left | Look Right ({SSVEP_FREQ_RIGHT}Hz) to Move Right"
    return fig, state, focus_text, instruction_text

def main():
    global board, eeg_channels, sampling_rate
    params = BrainFlowInputParams()
    params.timeout = 15  # Set a 15-second timeout for board connection
    board = BoardShim(BOARD_ID, params)
    
    try:
        print("Connecting to board...")
        board.prepare_session()
        
        eeg_channels = BoardShim.get_eeg_channels(BOARD_ID)
        sampling_rate = BoardShim.get_sampling_rate(BOARD_ID)
        
        print(f"Board connected. Sampling rate: {sampling_rate} Hz, EEG Channels: {eeg_channels}")
        
        print("Starting stream...")
        board.start_stream(450000) # Use BrainFlow's default buffer size
        time.sleep(3) # Wait for the buffer to fill with some initial data
        
        print("\nDash server is running. Open http://121.0.0.1:8050/ in your browser.")
        app.run(debug=False, use_reloader=False)

    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        if board and board.is_prepared():
            print("Stopping stream and releasing session.")
            board.stop_stream()
            board.release_session()

if __name__ == "__main__":
    main()
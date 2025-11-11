import time
from collections import deque
import numpy as np
from dash.exceptions import PreventUpdate

# --- BrainFlow and Machine Learning Imports ---
from brainflow.board_shim import BoardShim, BrainFlowInputParams, BoardIds
from brainflow.data_filter import DataFilter, FilterTypes
from sklearn.svm import SVC
from sklearn.preprocessing import StandardScaler

import plotly.graph_objs as go
from dash import Dash, dcc, html, Output, Input, State
import logging

# ==============================================================================
# === 1. TUNABLE CONFIGURATION ===============================================
# ==============================================================================
BOARD_ID = BoardIds.CERELOG_X8_BOARD # <-- IMPORTANT: CHANGE TO YOUR BOARD ID
GAME_INTERVAL_MS = 50
FOCUS_SMOOTHING_WINDOW = 15
BALL_SPEED = 15
CALIBRATION_SECONDS_PER_PHASE = 10
GAME_WIDTH = 800
GAME_HEIGHT = 200
BALL_RADIUS = 15

# --- BCI Signal Processing Config ---
DATA_WINDOW_SECONDS = 1.0
FILTER_LOW_CUT_HZ = 5.0
FILTER_HIGH_CUT_HZ = 45.0
FILTER_ORDER = 5
BRAINWAVE_BANDS = {"Alpha": [8, 12], "Beta": [13, 30]}

# ==============================================================================
# === 2. CORE SETUP ============================================================
# ==============================================================================
board = None
sampling_rate = 0
eeg_channels = []
fft_samples = 0
feature_vector_history = deque(maxlen=FOCUS_SMOOTHING_WINDOW)
ml_model = None
ml_scaler = None

app = Dash(__name__)
app.title = "BrainFlow BCI Ball Control"

def get_initial_game_state():
    return { 'ball_x': GAME_WIDTH / 2, 'ball_y': GAME_HEIGHT / 2, 'game_mode': 'CALIBRATE_RELAX', 'calibration_start_time': None, 'relax_readings': [], 'focus_readings': [] }

# --- THE FIX IS HERE ---
# The dcc.Graph ID is 'game-graph', which matches the callback below.
app.layout = html.Div(style={'backgroundColor': '#111', 'color': '#DDD', 'textAlign': 'center', 'fontFamily': 'monospace'}, children=[
    html.H1("BrainFlow BCI Ball Control"),
    html.P(id='instruction-text', children="Starting Calibration..."),
    dcc.Graph(id='game-graph', config={'staticPlot': True}), # <--- THIS ID WAS CHANGED
    dcc.Interval(id='game-interval', interval=GAME_INTERVAL_MS, n_intervals=0),
    dcc.Store(id='game-state-store', data=get_initial_game_state()),
    html.Div(id='focus-metric-display')
])

# ==============================================================================
# === 3. CORE GAME AND BCI LOGIC ===============================================
# ==============================================================================
@app.callback(
    Output('game-graph', 'figure'), # <--- THIS ID NOW MATCHES THE LAYOUT
    Output('game-state-store', 'data'),
    Output('focus-metric-display', 'children'),
    Output('instruction-text', 'children'),
    Input('game-interval', 'n_intervals'),
    State('game-state-store', 'data')
)
def update_game(n, state):
    global ml_model, ml_scaler
    if n is None or not board or not board.is_prepared(): raise PreventUpdate

    if board.get_board_data_count() < fft_samples:
        fig = go.Figure(); fig.update_layout(xaxis=dict(range=[0, GAME_WIDTH], visible=False), yaxis=dict(range=[0, GAME_HEIGHT], visible=False), plot_bgcolor='#000', paper_bgcolor='#111', annotations=[dict(text="Waiting for EEG Data...", x=GAME_WIDTH/2, y=GAME_HEIGHT/2, showarrow=False, font=dict(size=24, color='white'))])
        return fig, state, "Focus Metric: Waiting...", "Connecting to your brain..."

    data = board.get_current_board_data(fft_samples)
    if not eeg_channels: raise PreventUpdate
    
    ch_idx = eeg_channels[0]
    eeg_data = data[ch_idx].copy()
    DataFilter.perform_bandpass(eeg_data, sampling_rate, FILTER_LOW_CUT_HZ, FILTER_HIGH_CUT_HZ, FILTER_ORDER, FilterTypes.BUTTERWORTH, 0)

    y_data = eeg_data - np.mean(eeg_data)
    N = len(y_data)
    win = np.hanning(N)
    y_win = y_data * win
    
    yf = np.fft.fft(y_win)
    xf = np.fft.fftfreq(N, 1.0 / sampling_rate)[:N//2]
    psd = (2 / (sampling_rate * np.sum(win**2))) * np.abs(yf[0:N//2])**2
    df = sampling_rate / N

    alpha_mask = (xf >= BRAINWAVE_BANDS['Alpha'][0]) & (xf < BRAINWAVE_BANDS['Alpha'][1])
    beta_mask = (xf >= BRAINWAVE_BANDS['Beta'][0]) & (xf < BRAINWAVE_BANDS['Beta'][1])
    alpha_power = np.sum(psd[alpha_mask]) * df
    beta_power = np.sum(psd[beta_mask]) * df
    
    focus_metric = beta_power / alpha_power if alpha_power > 1e-12 else 0
    feature_vector = [focus_metric]

    current_time = n * GAME_INTERVAL_MS / 1000.0
    if state['calibration_start_time'] is None: state['calibration_start_time'] = current_time

    if state['game_mode'] == 'CALIBRATE_RELAX' or state['game_mode'] == 'CALIBRATE_FOCUS':
        time_in_phase = current_time - state['calibration_start_time']
        seconds_left = max(0, CALIBRATION_SECONDS_PER_PHASE - time_in_phase)
        instruction = "RELAX. Clear your mind." if state['game_mode'] == 'CALIBRATE_RELAX' else "FOCUS. Do mental math."
        if any(feature_vector):
            readings_key = 'relax_readings' if state['game_mode'] == 'CALIBRATE_RELAX' else 'focus_readings'
            state[readings_key].append(feature_vector)
        fig = go.Figure(); fig.update_layout(xaxis=dict(range=[0, GAME_WIDTH], visible=False), yaxis=dict(range=[0, GAME_HEIGHT], visible=False), plot_bgcolor='#000', paper_bgcolor='#111', annotations=[dict(text=f"{instruction}\n{int(seconds_left)}s left", x=GAME_WIDTH/2, y=GAME_HEIGHT/2, showarrow=False, font=dict(size=30, color='white'))])
        focus_text = f"Raw Focus Metric: {focus_metric:.2f}"
        if seconds_left == 0:
            if state['game_mode'] == 'CALIBRATE_RELAX': state['game_mode'] = 'CALIBRATE_FOCUS'; state['calibration_start_time'] = current_time
            else: state['game_mode'] = 'TRAINING_MODEL'
        return fig, state, focus_text, instruction

    elif state['game_mode'] == 'TRAINING_MODEL':
        X_relax, X_focus = np.array(state['relax_readings']), np.array(state['focus_readings'])
        if len(X_relax) < 20 or len(X_focus) < 20: return go.Figure(), get_initial_game_state(), "Calibration failed", "Not enough data. Restarting."
        X = np.vstack((X_relax, X_focus)); y = np.array([-1] * len(X_relax) + [1] * len(X_focus))
        ml_scaler = StandardScaler().fit(X); X_scaled = ml_scaler.transform(X)
        ml_model = SVC(kernel='rbf', C=1.0, probability=True).fit(X_scaled, y)
        state['game_mode'] = 'PLAYING'
   
    if state['game_mode'] == 'PLAYING':
        if ml_model is None or ml_scaler is None: return go.Figure(), get_initial_game_state(), "Recalibrating...", "Model not trained. Restarting."
        feature_vector_history.append(feature_vector)
        smoothed_feature_vector = np.mean(feature_vector_history, axis=0)
        features_scaled = ml_scaler.transform([smoothed_feature_vector])
        control_signal = ml_model.decision_function(features_scaled)[0]
        control_signal = np.clip(control_signal, -1.5, 1.5)
        state['ball_x'] += BALL_SPEED * control_signal
        state['ball_x'] = max(BALL_RADIUS, min(GAME_WIDTH - BALL_RADIUS, state['ball_x']))
        
        fig = go.Figure()
        fig.add_shape(type="rect", x0=0, y0=0, x1=GAME_WIDTH, y1=GAME_HEIGHT, fillcolor="#222", line=dict(width=0))
        fig.add_shape(type="circle", x0=state['ball_x']-BALL_RADIUS, y0=state['ball_y']-BALL_RADIUS, x1=state['ball_x']+BALL_RADIUS, y1=state['ball_y']+BALL_RADIUS, fillcolor="cyan", line=dict(width=0))
        fig.update_layout(xaxis=dict(range=[0, GAME_WIDTH], visible=False), yaxis=dict(range=[0, GAME_HEIGHT], visible=False), plot_bgcolor='#000', paper_bgcolor='#111', margin=dict(l=10, r=10, t=10, b=10))
        
        focus_text = f"Control Signal: {control_signal:.2f}"
        instruction_text = "Relax to move left. Focus to move right."
        return fig, state, focus_text, instruction_text
    return PreventUpdate

# ==============================================================================
# === 4. MAIN EXECUTION ========================================================
# ==============================================================================
def main():
    global board, sampling_rate, eeg_channels, fft_samples
    params = BrainFlowInputParams()
    params.timeout = 15
    try:
        board = BoardShim(BOARD_ID, params)
        print("Connecting to board...")
        board.prepare_session()
        sampling_rate = BoardShim.get_sampling_rate(BOARD_ID)
        eeg_channels = BoardShim.get_eeg_channels(BOARD_ID)
        fft_samples = int(sampling_rate * DATA_WINDOW_SECONDS)
        
        print(f"Board connected. Sampling Rate: {sampling_rate} Hz")
        print(f"Found EEG Channels: {eeg_channels}")
        if eeg_channels: print(f"--> Using Channel: {eeg_channels[0]} for control.")
        else: print("--> ERROR: No EEG channels found for this board. Exiting."); return
        print(f"FFT window size: {fft_samples} samples")
        
        print("Starting data stream...")
        board.start_stream(450000)
        time.sleep(DATA_WINDOW_SECONDS + 1.0)
        
        log = logging.getLogger('werkzeug'); log.setLevel(logging.ERROR)
        print("\nDash server is running. Open http://127.0.0.1:8050/ in your browser.")
        app.run(debug=False, use_reloader=False)
    except Exception as e:
        print(f"An error occurred: {e}")
        print("Please check your Board ID and hardware connection.")
    finally:
        if board and board.is_prepared():
            print("Stopping stream and releasing session.")
            board.stop_stream()
            board.release_session()

if __name__ == "__main__":
    main()
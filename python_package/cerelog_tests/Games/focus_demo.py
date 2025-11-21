import time
from collections import deque
import numpy as np
from dash.exceptions import PreventUpdate
import enum 
# --- BrainFlow and Machine Learning Imports ---
from brainflow.board_shim import BoardShim, BrainFlowInputParams, BoardIds
from brainflow.data_filter import DataFilter, FilterTypes
from brainflow.data_filter import NoiseTypes, DetrendOperations, AggOperations, WaveletTypes, NoiseEstimationLevelTypes, WaveletExtensionTypes, ThresholdTypes, WaveletDenoisingTypes
from sklearn.svm import SVC
from sklearn.preprocessing import StandardScaler

import plotly.graph_objs as go
from dash import Dash, dcc, html, Output, Input, State
import logging

class DetrendOperations(enum.IntEnum):
    """Enum to store all supported detrend options"""

    NO_DETREND = 0  #:
    CONSTANT = 1  #:
    LINEAR = 2  #:
# ==============================================================================
# === 1. TUNABLE CONFIGURATION ===============================================
# ==============================================================================
BOARD_ID = BoardIds.CERELOG_X8_BOARD
GAME_INTERVAL_MS = 50
FOCUS_SMOOTHING_WINDOW = 15
CALIBRATION_SECONDS_PER_PHASE = 10
GAME_WIDTH = 400  # Narrower for a vertical game
GAME_HEIGHT = 800

# --- New Game Physics & Rules ---
GRAVITY = -0.4             # Constant downward force
LIFT_FORCE = 1.2           # How strongly "focus" pushes the ball up
DAMPING = 0.98             # Air resistance/friction to prevent infinite speed
TARGET_ZONE_Y = GAME_HEIGHT - 150 # Y-coordinate of the bottom of the target zone
TARGET_ZONE_HEIGHT = 100
SCORE_THRESHOLD_SECONDS = 2.0 # How long to hold the ball in the zone to score

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
app.title = "BrainFlow BCI: Tube Game"

def get_initial_game_state():
    return {
        'ball_y': 50, 'ball_vy': 0, 'score': 0, 'time_in_zone': 0,
        'game_mode': 'CALIBRATE_RELAX', 'calibration_start_time': None,
        'relax_readings': [], 'focus_readings': [],
    }

app.layout = html.Div(style={'backgroundColor': '#111', 'color': '#DDD', 'textAlign': 'center', 'fontFamily': 'monospace'}, children=[
    html.H1("BrainFlow BCI: Tube Game"),
    html.P(id='instruction-text', children="Starting Calibration..."),
    dcc.Graph(id='game-graph', config={'staticPlot': True}),
    dcc.Interval(id='game-interval', interval=GAME_INTERVAL_MS, n_intervals=0),
    dcc.Store(id='game-state-store', data=get_initial_game_state()),
    html.Div(id='focus-metric-display')
])

# ==============================================================================
# === 3. CORE GAME AND BCI LOGIC ===============================================
# ==============================================================================
@app.callback(
    Output('game-graph', 'figure'),
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
    feature_vector = []
    
    for ch_idx in eeg_channels:
        eeg_data = data[ch_idx].copy()

        # ----------------- START OF EXACT REPLICATION FROM YOUR PLOTTER -----------------
        # Apply the band-stop (notch) filter for 60 Hz noise, exactly as done in your plotter.
        center_freq = 60.0
        bandwidth = 4.0
        start_freq = center_freq - (bandwidth / 2.0)
        stop_freq = center_freq + (bandwidth / 2.0)
        # Using filter order 3, same as your plotter
        
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

        # ------------------ END OF EXACT REPLICATION ------------------

        DataFilter.perform_bandpass(eeg_data, sampling_rate, FILTER_LOW_CUT_HZ, FILTER_HIGH_CUT_HZ, FILTER_ORDER, FilterTypes.BUTTERWORTH, 0)
        DataFilter.detrend(eeg_data, DetrendOperations.CONSTANT.value)

        
        
        if np.isnan(eeg_data).any():
            print(f"Warning: NaN detected in channel {ch_idx} after filtering. Skipping update.")
            raise PreventUpdate

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
        feature_vector.append(focus_metric)

    current_time = n * GAME_INTERVAL_MS / 1000.0
    if state['calibration_start_time'] is None:
        state['calibration_start_time'] = current_time

    if state['game_mode'] in ['CALIBRATE_RELAX', 'CALIBRATE_FOCUS']:
        time_in_phase = current_time - state['calibration_start_time']
        seconds_left = max(0, CALIBRATION_SECONDS_PER_PHASE - time_in_phase)
        instruction = "RELAX. Clear your mind." if state['game_mode'] == 'CALIBRATE_RELAX' else "FOCUS. Do mental math."
        if any(feature_vector):
            readings_key = 'relax_readings' if state['game_mode'] == 'CALIBRATE_RELAX' else 'focus_readings'
            state[readings_key].append(feature_vector)
        fig = go.Figure()
        fig.update_layout(xaxis=dict(range=[0, GAME_WIDTH], visible=False), yaxis=dict(range=[0, GAME_HEIGHT], visible=False), plot_bgcolor='#000', paper_bgcolor='#111', annotations=[dict(text=f"{instruction}\n{int(seconds_left)}s left", x=GAME_WIDTH/2, y=GAME_HEIGHT/2, showarrow=False, font=dict(size=30, color='white'))])
        focus_text = f"Avg Raw Focus Power Ratio: {np.mean(feature_vector):.2f}"
        if seconds_left == 0:
            if state['game_mode'] == 'CALIBRATE_RELAX':
                state['game_mode'] = 'CALIBRATE_FOCUS'
                state['calibration_start_time'] = current_time
            else:
                state['game_mode'] = 'TRAINING_MODEL'
        return fig, state, focus_text, instruction

    elif state['game_mode'] == 'TRAINING_MODEL':
        X_relax, X_focus = np.array(state['relax_readings']), np.array(state['focus_readings'])
        if len(X_relax) < 20 or len(X_focus) < 20:
            return go.Figure(), get_initial_game_state(), "Calibration failed", "Not enough data. Restarting."
        X = np.vstack((X_relax, X_focus))
        y = np.array([-1] * len(X_relax) + [1] * len(X_focus))
        ml_scaler = StandardScaler().fit(X)
        X_scaled = ml_scaler.transform(X)
        ml_model = SVC(kernel='rbf', C=1.0, probability=True).fit(X_scaled, y)
        state['game_mode'] = 'PLAYING'
   
    if state['game_mode'] == 'PLAYING':
        if ml_model is None or ml_scaler is None:
            return go.Figure(), get_initial_game_state(), "Recalibrating...", "Server restarted."
        
        feature_vector_history.append(feature_vector)
        smoothed_feature_vector = np.mean(feature_vector_history, axis=0)
        features_scaled = ml_scaler.transform([smoothed_feature_vector])
        control_signal = ml_model.decision_function(features_scaled)[0]
        
        state['ball_vy'] += GRAVITY
        if control_signal > 0:
            state['ball_vy'] += LIFT_FORCE * control_signal
        state['ball_vy'] *= DAMPING
        state['ball_y'] += state['ball_vy']
        if state['ball_y'] < 20:
            state['ball_y'] = 20; state['ball_vy'] = 0
        if state['ball_y'] > GAME_HEIGHT - 20:
            state['ball_y'] = GAME_HEIGHT - 20; state['ball_vy'] *= -0.5

        is_in_zone = TARGET_ZONE_Y <= state['ball_y'] <= TARGET_ZONE_Y + TARGET_ZONE_HEIGHT
        if is_in_zone:
            state['time_in_zone'] += GAME_INTERVAL_MS / 1000.0
            if state['time_in_zone'] >= SCORE_THRESHOLD_SECONDS:
                state['score'] += 1
                state['ball_y'] = 50; state['ball_vy'] = 0
                state['time_in_zone'] = 0
        else:
            state['time_in_zone'] = 0

        fig = go.Figure()
        fig.add_shape(type="rect", x0=0, y0=0, x1=20, y1=GAME_HEIGHT, fillcolor="#333")
        fig.add_shape(type="rect", x0=GAME_WIDTH-20, y0=0, x1=GAME_WIDTH, y1=GAME_HEIGHT, fillcolor="#333")
        fig.add_shape(type="rect", x0=20, y0=TARGET_ZONE_Y, x1=GAME_WIDTH-20, y1=TARGET_ZONE_Y + TARGET_ZONE_HEIGHT, fillcolor="rgba(0, 255, 255, 0.3)", line_width=0)
        ball_radius = 20
        fig.add_shape(type="circle", x0=GAME_WIDTH/2 - ball_radius, y0=state['ball_y'] - ball_radius, x1=GAME_WIDTH/2 + ball_radius, y1=state['ball_y'] + ball_radius, fillcolor="white", line_width=0)
        
        fig.update_layout(xaxis=dict(range=[0, GAME_WIDTH], visible=False), yaxis=dict(range=[0, GAME_HEIGHT], visible=False), plot_bgcolor='#000', paper_bgcolor='#111', margin=dict(l=10, r=10, t=10, b=10),
                          annotations=[dict(text=f"Score: {state['score']}", x=GAME_WIDTH/2, y=50, showarrow=False, font=dict(size=40, color='cyan'))])
       
        focus_text = f"Focus Control Signal: {control_signal:.2f}"
        instruction_text = "Focus to make the ball rise into the target zone!"
        return fig, state, focus_text, instruction_text

    return PreventUpdate

# ==============================================================================
# === 4. MAIN EXECUTION ========================================================
# ==============================================================================
def main():
    global board, sampling_rate, eeg_channels, fft_samples
    params = BrainFlowInputParams()
    params.timeout = 15
    board = BoardShim(BOARD_ID, params)
    try:
        print("Connecting to board...")
        board.prepare_session()
        sampling_rate = BoardShim.get_sampling_rate(BOARD_ID)
        eeg_channels = BoardShim.get_eeg_channels(BOARD_ID)
        fft_samples = int(sampling_rate * DATA_WINDOW_SECONDS)
        print(f"Board connected. Sampling Rate: {sampling_rate} Hz")
        print(f"Using EEG Channels: {eeg_channels}")
        print(f"FFT window size: {fft_samples} samples")
        print("Starting data stream...")
        board.start_stream(450000)
        time.sleep(DATA_WINDOW_SECONDS + 1.0)
        log = logging.getLogger('werkzeug')
        log.setLevel(logging.ERROR)
        print("\nDash server is running. Open http://127.0.0.1:8050/ in your browser.")
        app.run(debug=False, use_reloader=False)
    except Exception as e:
        print(f"An error occurred during setup: {e}")
    finally:
        if board and board.is_prepared():
            print("Stopping stream and releasing session.")
            board.stop_stream()
            board.release_session()

if __name__ == "__main__":
    main()
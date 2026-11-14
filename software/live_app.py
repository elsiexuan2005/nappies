"""Live dashboard.

  streamlit run live_app.py

Sources (sidebar):
  Simulator  - no hardware needed; Wee / Poo buttons, asleep toggle, speed-up (safe demo fallback)
  Serial     - your board on USB
  Replay CSV - play back a capture or synthetic CSV
"""
import os
import threading
import time

import joblib
import numpy as np
import pandas as pd
import streamlit as st

import config as C
from features import compute_features
from recommend import recommend
from sensor import load_cal, sat_from_raw
from simulate import SimPod

st.set_page_config(page_title="Smart Diaper Pod", layout="wide")
COLOR = {"green": "#1f9d55", "orange": "#e08a00", "red": "#d64545"}


@st.cache_resource
def load_models():
    return joblib.load("models.joblib"), load_cal()


models, cal = load_models()
ss = st.session_state


# ---------------- sources ----------------
class SerialSource:
    """Arduino (moisture+gas) and Nicla (IMU) on separate USB ports, merged."""

    def __init__(self, ports):
        from pod_stream import PodStream
        self.ps = PodStream(ports)

    def read(self, speed=1):
        return self.ps.read()


class ReplaySource:
    def __init__(self, path):
        self.df = pd.read_csv(path)
        self.i = 0

    def read(self, speed=1):
        chunk = self.df.iloc[self.i:self.i + speed]
        self.i += len(chunk)
        return [{"t": float(self.i - len(chunk) + k), "moisture": r.moisture, "gas": r.gas,
                 "acc_mean": r.acc_mean, "acc_std": r.acc_std, "gyro_mean": r.gyro_mean}
                for k, r in enumerate(chunk.itertuples())]


class SimSource:
    def __init__(self, age, size):
        self.pod = SimPod(cal, age=age, size=size)

    def read(self, speed=1):
        return [self.pod.step() for _ in range(speed)]


# ---------------- sidebar ----------------
st.sidebar.title("Smart Diaper Pod")
mode = st.sidebar.radio("Data source", ["Simulator", "Serial", "Replay CSV"])
age = st.sidebar.selectbox("Baby age", list(C.AGE_GROUPS), index=1)
size = st.sidebar.selectbox("Diaper size", list(C.DIAPER_CAPACITY_ML), index=2)

key = (mode, age, size)
if mode == "Serial":
    ports_txt = st.sidebar.text_input("Ports (Arduino, Nicla)", "auto",
                                      help="Run `python pod_stream.py --list` to see them. Order doesn't matter.")
    port = [p.strip() for p in ports_txt.split(",") if p.strip()]
    key += tuple(port)
elif mode == "Replay CSV":
    path = st.sidebar.text_input("CSV path", "synthetic_sample.csv")
    key += (path,)

if ss.get("key") != key:
    try:
        ss.src = (SimSource(age, size) if mode == "Simulator"
                  else SerialSource(port) if mode == "Serial" else ReplaySource(path))
        ss.key, ss.buf, ss.err = key, [], None
    except Exception as e:  # noqa: BLE001
        ss.src, ss.err, ss.key = None, str(e), key

speed = 1
if mode == "Simulator" and ss.src:
    pod = ss.src.pod
    speed = st.sidebar.select_slider("Speed-up", [1, 10, 30, 60, 120, 300, 600], value=60)
    c1, c2 = st.sidebar.columns(2)
    if c1.button("Wee", use_container_width=True):
        pod.wee()
    if c2.button("Poo", use_container_width=True):
        pod.poo()
    pod.asleep = st.sidebar.toggle("Baby asleep", value=pod.asleep)
    pod.auto = st.sidebar.toggle("Random events", value=pod.auto)
elif mode == "Replay CSV":
    speed = st.sidebar.select_slider("Speed-up", [1, 10, 30, 60, 120, 300], value=60)

if st.sidebar.button("Diaper changed", type="primary", use_container_width=True):
    ss.buf = []
    if mode == "Simulator" and ss.src:
        ss.src.pod.change()

if ss.get("err"):
    st.error(f"Could not open source: {ss.err}")
    st.stop()



# ---------------- main loop ----------------
def render(buf):
    if len(buf) < 30:
        st.info(f"Collecting baseline after diaper change... ({len(buf)}/30 s)")
        return
    df = pd.DataFrame(buf)
    f = compute_features(df, cal, size, step=1)
    last = f.iloc[[-1]]
    F = models["features"]
    probs = models["state"].predict_proba(last[F["state"]])[0]
    state = models["states"][int(models["state"].classes_[int(np.argmax(probs))])]
    moving = bool(models["act"].predict(last[F["act"]])[0])
    p_leak = float(models["leak"].predict_proba(last[F["leak"]])[0, 1])
    ttl = float(np.clip(np.expm1(models["ttl"].predict(last[F["ttl"]])[0]), 0, C.TTL_CAP_MIN))
    sat = float(last["sat"].iloc[0])
    still_frac = float(last["still_frac10"].iloc[0])
    title, detail, color = recommend(state, sat, p_leak, ttl, still_frac)

    st.markdown(
        f"<div style='background:{COLOR[color]};color:white;padding:18px 24px;border-radius:12px'>"
        f"<div style='font-size:34px;font-weight:700'>{title}</div>"
        f"<div style='font-size:18px'>{detail}</div></div>", unsafe_allow_html=True)

    mins = float(last["mins"].iloc[0])
    c = st.columns(5)
    c[0].metric("Diaper state", state.upper(), f"{probs.max():.0%} confident", delta_color="off")
    c[1].metric("Saturation", f"{sat:.0%}")
    c[2].metric("Full within 1 h", "FULL" if sat >= C.LEAK_SAT else f"{p_leak:.0%}")
    c[3].metric("Baby", "moving" if moving else ("asleep" if still_frac > 0.8 else "still"))
    c[4].metric("Since change", f"{int(mins // 60)}h {int(mins % 60):02d}m")

    step = max(1, len(f) // 600)
    chart = f.iloc[::step].set_index("mins")
    l, r = st.columns(2)
    l.caption("Moisture saturation (0-1)")
    l.line_chart(chart[["sat"]], height=220)
    r.caption("Gas rise since change (ammonia / VOC)")
    r.line_chart(chart[["gas_rise"]], height=220)
    st.caption("State probabilities: " + "  |  ".join(
        f"{models['states'][int(k)]} {p:.0%}" for k, p in zip(models["state"].classes_, probs)))


placeholder = st.empty()
for _ in range(int(os.environ.get("DIAPER_MAX_LOOPS", "1000000000"))):
    ss.buf.extend(ss.src.read(speed))
    ss.buf = ss.buf[-14 * 3600:]
    with placeholder.container():
        if mode == "Serial":
            s_ = ss.src.ps.status()
            st.caption(f"Sensor board: {'connected' if s_['sensors'] else 'NO DATA'}  |  "
                       f"Nicla IMU: {'connected' if s_['imu'] else 'NO DATA'}")
        render(ss.buf)
    time.sleep(1)

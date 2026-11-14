"""Feature extraction - the SAME code runs on synthetic data (training)
and on live serial data (inference), so the model sees identical inputs.

Input: one diaper session (rows since the last change), 1 row per second,
columns t, moisture, gas, acc_std, gyro_mean.
"""
import numpy as np
import pandas as pd

from sensor import gas_norm, sat_from_raw, still_threshold

FEATURES_STATE = ["sat", "sat_max", "sat_slope60", "gas_rise", "gas_slope60", "mins"]
FEATURES_ACT = ["acc_std_mean30", "acc_std_max30", "gyro_mean30"]
FEATURES_TTL = ["sat", "sat_max", "mins", "sat_rate_hr", "still_frac10", "size"]
FEATURES_LEAK = ["sat", "sat_max", "still_frac10", "size"]   # no elapsed-time or rate features -> robust to sped-up demos


def compute_features(df, cal, size, step=10):
    t = df["t"].to_numpy(float)
    mins = (t - t[0]) / 60.0

    s = pd.Series(sat_from_raw(df["moisture"].to_numpy(float), cal))
    sat = s.rolling(10, min_periods=1).median()
    sat_max = s.rolling(30, min_periods=1).median().cummax()
    sat_slope60 = sat - sat.shift(60).fillna(sat.iloc[0])

    g = pd.Series(gas_norm(df["gas"].to_numpy(float), cal)).rolling(10, min_periods=1).mean()
    g0 = g.iloc[:30].median()                          # baseline at diaper change
    gas_rise = g - g0
    gas_slope60 = g - g.shift(60).fillna(g.iloc[0])

    a = pd.Series(df["acc_std"].to_numpy(float))
    gy = pd.Series(df["gyro_mean"].to_numpy(float))
    still = (a < still_threshold(cal)).astype(float)

    out = pd.DataFrame({
        "mins": mins,
        "sat": sat, "sat_max": sat_max, "sat_slope60": sat_slope60,
        "gas_rise": gas_rise, "gas_slope60": gas_slope60,
        "acc_std_mean30": a.rolling(30, min_periods=1).mean(),
        "acc_std_max30": a.rolling(30, min_periods=1).max(),
        "gyro_mean30": gy.rolling(30, min_periods=1).mean(),
        "still_frac10": still.rolling(600, min_periods=1).mean(),
        "sat_rate_hr": sat / np.maximum(mins / 60.0, 0.25),
        "size": float(size),
    })
    return out.iloc[::step]

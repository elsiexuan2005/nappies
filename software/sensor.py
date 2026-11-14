"""Calibration: converts YOUR board's raw readings into sensor-independent units.

moisture raw -> saturation 0..1   (3-point curve: dry, 50 %, 100 %)
gas raw      -> gas_norm          (0 = clean-air baseline, 1 = strong VOC source)
"""
import json
import os
import warnings

import numpy as np

# Placeholder values so everything runs before you calibrate.
# Replace by running: python calibrate.py captures/*.csv
# Defaults assume the team's Arduino UNO Q (12-bit ADC, 0-4095, 3.3 V reference).
# This board's moisture probe reads LOW in air (~165) and HIGHER in water
# (opposite of DFRobot SEN0193 wiki). sat_from_raw handles either polarity.
# Gas values are rough guesses - calibrate!
DEFAULT_CAL = {
    "moisture": {"dry": 150.0, "wet50": 900.0, "wet100": 1600.0, "noise": 12.0},
    "gas": {"base": 600.0, "voc": 1800.0, "noise": 8.0},
    "imu": {"still_acc_std": 0.004, "active_acc_std": 0.15,
            "still_gyro": 0.6, "active_gyro": 45.0},
}


def load_cal(path="calibration.json"):
    cal = json.loads(json.dumps(DEFAULT_CAL))
    if os.path.exists(path):
        with open(path) as f:
            user = json.load(f)
        for k, v in user.items():
            cal.setdefault(k, {}).update(v)
    else:
        warnings.warn(f"{path} not found - using placeholder calibration")
    _check(cal)
    return cal


def _check(cal):
    m = cal["moisture"]
    lo, hi = sorted([m["dry"], m["wet100"]])
    if not (lo <= m["wet50"] <= hi):
        warnings.warn("wet50 is not between dry and wet100 - using 2-point curve")
        m["wet50"] = (m["dry"] + m["wet100"]) / 2


def _moisture_points(cal):
    m = cal["moisture"]
    return np.array([m["dry"], m["wet50"], m["wet100"]], float), np.array([0.0, 0.5, 1.0])


def sat_from_raw(raw, cal):
    """Raw ADC -> saturation 0..1. Works whether wet reads higher or lower."""
    xp, fp = _moisture_points(cal)
    if xp[0] > xp[-1]:          # capacitive sensors read LOWER when wet
        xp, fp = xp[::-1], fp[::-1]
    return np.interp(raw, xp, fp)


def raw_from_sat(sat, cal):
    xp, fp = _moisture_points(cal)
    return np.interp(sat, fp, xp)


def gas_norm(raw, cal):
    g = cal["gas"]
    return (np.asarray(raw, float) - g["base"]) / max(1e-6, g["voc"] - g["base"])


def still_threshold(cal):
    """acc_std below this = baby is still (geometric mean of still & active)."""
    i = cal["imu"]
    return float(np.sqrt(i["still_acc_std"] * i["active_acc_std"]))

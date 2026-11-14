"""Build calibration.json from labelled captures.

  python calibrate.py captures/*.csv
  python calibrate.py ../data/*.csv          # recordings from hardware/record_sensor.py also work

Accepted files:
  capture.py   columns host_time,label,moisture,gas,...
  record_sensor.py (team tool)  columns recorded_at,timestamp_ms,sensor_raw,experiment
                 the label comes from the "experiment" column (or the file name if it's empty);
                 names like "dry", "50%", "50_water", "100", "full_water" are recognised.

Uses labels dry, wet50, wet100, gas_base, gas_voc, still, active.
Drops the first 5 s after every label switch (sensor still settling).
Any label you didn't record keeps its placeholder value.
"""
import json
import os
import re
import sys

import pandas as pd

from sensor import DEFAULT_CAL


def robust_std(x):
    """Spread that ignores a few mislabelled transition rows (MAD * 1.4826)."""
    return float(1.4826 * (x - x.median()).abs().median())


def normalise_label(name):
    """Map free-text experiment names to calibration labels."""
    n = str(name).strip().lower()
    if not n or n == "nan":
        return "unlabeled"
    if n in {"dry", "wet50", "wet100", "gas_base", "gas_voc", "still", "active"}:
        return n
    if re.search(r"(^|[^0-9])0+(%|_|\b)|dry|air", n) and "100" not in n:
        return "dry"
    if re.search(r"100|full|soak|saturat|super|very_wet", n):
        return "wet100"
    if re.search(r"50|half", n):
        return "wet50"
    if re.search(r"voc|vinegar|alcohol|poo|smell", n):
        return "gas_voc"
    if re.search(r"base|clean", n):
        return "gas_base"
    if re.search(r"still|rest", n):
        return "still"
    if re.search(r"move|shak|active", n):
        return "active"
    return n


def load(path):
    df = pd.read_csv(path)
    if "label" in df:                                   # capture.py
        return df
    if "sensor_raw" in df or "gas_raw" in df:           # hardware/record_sensor.py
        exp = df["experiment"].fillna("").astype(str) if "experiment" in df else pd.Series([""] * len(df))
        if (exp.str.strip() == "").all():               # EXPERIMENT left empty -> use file name
            stem = os.path.splitext(os.path.basename(path))[0]
            exp = pd.Series([re.sub(r"^\d{8}_\d{6}_?", "", stem)] * len(df))
        out = pd.DataFrame({"label": exp.map(normalise_label)})
        out["moisture"] = df["sensor_raw"] if "sensor_raw" in df else float("nan")
        out["gas"] = df["gas_raw"] if "gas_raw" in df else float("nan")
        for c in ("acc_mean", "acc_std", "gyro_mean"):
            out[c] = float("nan")
        print(f"{os.path.basename(path)}: record_sensor.py format, label '{out['label'].iloc[0]}'")
        return out
    raise SystemExit(f"{path}: unknown CSV format")


def main(paths):
    keep = []
    for p in paths:
        if re.search(r"no[_ -]?sensor", os.path.basename(p), re.I):
            print(f"skipping {os.path.basename(p)}: recorded with no sensor connected (floating pin = noise)")
            continue
        keep.append(p)
    if not keep:
        raise SystemExit("no usable recordings")
    df = pd.concat([load(p) for p in keep], ignore_index=True)
    seg = (df["label"] != df["label"].shift()).cumsum()
    df = df[df.groupby(seg).cumcount() >= 5]
    by = {k: g for k, g in df.groupby("label")}
    cal = json.loads(json.dumps(DEFAULT_CAL))
    used = []

    def med(label, col, q=0.5):
        if label in by and by[label][col].notna().sum() >= 10:
            used.append(label)
            return float(by[label][col].dropna().quantile(q))
        return None

    m = cal["moisture"]
    got = {lab: med(lab, "moisture") for lab in ["dry", "wet50", "wet100"]}
    if got["dry"] is not None and (got["wet100"] is not None or got["wet50"] is not None):
        m["dry"] = got["dry"]
        if got["wet100"] is not None:
            m["wet100"] = got["wet100"]
            m["wet50"] = got["wet50"] if got["wet50"] is not None else (m["dry"] + m["wet100"]) / 2
        else:                                    # only 50 %: extrapolate the full-wet point
            m["wet50"] = got["wet50"]
            m["wet100"] = m["dry"] + 2 * (got["wet50"] - m["dry"])
    elif any(v is not None for v in got.values()):
        print("WARNING: moisture needs a DRY recording and at least one WET recording (50% or 100%). "
              "Moisture calibration NOT updated - mixing real and default values would be wrong.")
    stds = [robust_std(by[l]["moisture"].dropna()) for l in ["dry", "wet50", "wet100"]
            if l in by and by[l]["moisture"].notna().sum() >= 10]
    if stds:
        m["noise"] = float(max(1.0, pd.Series(stds).median()))

    g = cal["gas"]
    b = med("gas_base", "gas")
    if b is None:
        b = med("dry", "gas")
    if b is not None:
        g["base"] = b
        src = by["gas_base"] if "gas_base" in by and len(by["gas_base"]) >= 10 else by["dry"]
        g["noise"] = max(1.0, robust_std(src["gas"].dropna()))
    v = med("gas_voc", "gas", q=0.9)      # peak-ish: the rise takes time
    if v is not None:
        g["voc"] = v

    i = cal["imu"]
    for lab, a_key, g_key in [("still", "still_acc_std", "still_gyro"), ("active", "active_acc_std", "active_gyro")]:
        a_v, g_v = med(lab, "acc_std"), med(lab, "gyro_mean")
        if a_v is not None:
            i[a_key], i[g_key] = max(a_v, 1e-4), max(g_v, 1e-2)

    with open("calibration.json", "w") as f:
        json.dump(cal, f, indent=2)
    print(json.dumps(cal, indent=2))
    print("labels used:", sorted(set(used)))
    missing = {"dry", "wet50", "wet100", "gas_base", "gas_voc", "still", "active"} - set(used)
    if missing:
        print("still placeholder for:", sorted(missing))
    if m["wet100"] > m["dry"]:
        print("NOTE: this moisture sensor reads HIGHER when wetter (resistive-style). That's fine - "
              "the calibration handles either direction.")
    if abs(g["voc"] - g["base"]) < 5 * g["noise"]:
        print("WARNING: gas_voc barely differs from baseline - hold the swab closer / let the sensor warm up")
    print("saved calibration.json -> now run: python train.py")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1:])

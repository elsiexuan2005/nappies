"""Shared bench analysis: run the models over labelled bench runs (validation)
and extract product-performance metrics (Product Lab).

Used by validate.py (report + plots) and server.py (/api/lab).
"""
import glob
import os

import joblib
import numpy as np
import pandas as pd

import config as C
from features import compute_features
from sensor import load_cal

GRACE_S = 30          # ignore rows this soon after a truth change (sensor + smoothing delay)
SUSTAIN_S = 5         # a detection / false alarm must last this long to count
WEE_JUMP = 0.04


def load_runs(patterns=("bench/*.csv", "bench/examples/*.csv")):
    runs = []
    for pat in patterns:
        for path in sorted(glob.glob(pat)):
            try:
                df = pd.read_csv(path)
            except Exception:  # noqa: BLE001
                continue
            if df.empty or "truth" not in df:
                continue
            df["event"] = df["event"].fillna("")
            df["t"] = df["host_time"].astype(float)
            df["acc_std"] = df["acc_std"].fillna(0.0)
            df["gyro_mean"] = df["gyro_mean"].fillna(0.0)
            df["acc_mean"] = df["acc_mean"].fillna(1.0)
            runs.append({"path": path, "id": str(df["run_id"].iloc[0]), "nappy": str(df["nappy"].iloc[0]),
                         "placement": str(df["placement"].iloc[0]), "protocol": str(df["protocol"].iloc[0]),
                         "source": str(df["source"].iloc[0]), "df": df})
    return runs


def predict(run, M=None, cal=None, size=3):
    """Per-row model outputs for a run (the run is treated as one diaper session)."""
    M = M or joblib.load("models.joblib")
    cal = cal or load_cal()
    df = run["df"]
    f = compute_features(df, cal, size, step=1).reset_index(drop=True)
    F = M["features"]
    probs = M["state"].predict_proba(f[F["state"]])
    cls = np.array(M["states"])[M["state"].classes_[probs.argmax(1)].astype(int)]
    out = pd.DataFrame({
        "t": df["t"].to_numpy(), "rel_s": df["t"].to_numpy() - df["t"].iloc[0],
        "truth": df["truth"].to_numpy(), "event": df["event"].to_numpy(), "phase": df["phase"].to_numpy(),
        "pred": cls, "conf": probs.max(1),
        "moving": M["act"].predict(f[F["act"]]).astype(bool),
        "p_full": M["leak"].predict_proba(f[F["leak"]])[:, 1],
        "sat": f["sat"].to_numpy(), "sat_max": f["sat_max"].to_numpy(), "gas_rise": f["gas_rise"].to_numpy(),
        "hum": pd.to_numeric(df.get("hum"), errors="coerce").to_numpy() if "hum" in df else np.nan,
        "temp": pd.to_numeric(df.get("temp"), errors="coerce").to_numpy() if "temp" in df else np.nan,
    })
    out["alert"] = (out["pred"] == "soiled") | (out["sat"] >= C.LEAK_SAT) | (out["p_full"] > 0.7)
    return out


def _episodes(mask, rel_s):
    """Count runs of True lasting >= SUSTAIN_S seconds."""
    n, start = 0, None
    for m, t in zip(list(mask) + [False], list(rel_s) + [None]):
        if m and start is None:
            start = t
        elif not m and start is not None:
            if t is None or t - start >= SUSTAIN_S:
                n += 1
            start = None
    return n


def validate_run(run, P):
    """Compare model output to the labelled truth for one run."""
    truth, pred, rel = P["truth"].to_numpy(), P["pred"].to_numpy(), P["rel_s"].to_numpy()
    change_t = [rel[0]] + [rel[i] for i in range(1, len(truth)) if truth[i] != truth[i - 1]]
    since_change = np.array([t - max(c for c in change_t if c <= t) for t in rel])
    keep = since_change >= GRACE_S
    acc = float((truth[keep] == pred[keep]).mean()) if keep.any() else None

    # detection delay after each event that changes the truth
    delays = []
    for i in np.nonzero(P["event"].to_numpy() != "")[0]:
        ev = P["event"].iloc[i]
        if not (ev.startswith("insult") or ev == "poo"):
            continue
        target = "soiled" if ev == "poo" else "wet"
        if ev.startswith("insult") and ev != "insult1":
            continue                           # only the first insult changes dry -> wet
        ok = pred[i:] == target
        found = None
        for k in range(len(ok) - SUSTAIN_S):
            if ok[k:k + SUSTAIN_S].all():
                found = rel[i + k] - rel[i]
                break
        delays.append({"event": ev, "target": target, "delay_s": found})

    # false alarms: predicted a worse state than the truth (sustained)
    order = {"dry": 0, "wet": 1, "soiled": 2}
    worse = np.array([order.get(p, 0) > order.get(t, 0) for p, t in zip(pred, truth)]) & keep
    false_alarms = _episodes(worse, rel)
    false_change_alerts = _episodes(P["alert"].to_numpy() & (truth == "dry") & keep, rel)

    # wee events detected (same rule as the live engine) vs actual insults
    detected, hw, last = 0, 0.0, -1e9
    for t, m in zip(rel, P["sat_max"].to_numpy()):
        if m > hw + WEE_JUMP and t - last > 180:
            detected += 1
            last = t
        hw = max(hw, m)
    insults = int(sum(e.startswith("insult") for e in P["event"]))

    moving = P["phase"].to_numpy() == "moving"
    return {
        "id": run["id"], "nappy": run["nappy"], "protocol": run["protocol"], "source": run["source"],
        "placement": run["placement"], "minutes": round(float(rel[-1]) / 60, 1),
        "accuracy": None if acc is None else round(acc, 3),
        "delays": delays, "false_alarms": false_alarms, "false_change_alerts": false_change_alerts,
        "moving_minutes": round(float(moving.sum()) / 60, 1),
        "motion_detected": round(float(P["moving"].to_numpy()[moving].mean()), 2) if moving.any() else None,
        "wees_detected": detected, "insults": insults,
    }


def summarize_validation(results):
    """Headline numbers across runs. Real bench runs count; simulated examples only if nothing else."""
    real = [r for r in results if r["source"] == "bench"]
    use, only_examples = (real, False) if real else (results, True)
    if not use:
        return {"runs": 0, "only_examples": True}
    accs = [r["accuracy"] for r in use if r["accuracy"] is not None]
    wet_d = [d["delay_s"] for r in use for d in r["delays"] if d["target"] == "wet" and d["delay_s"] is not None]
    poo_d = [d["delay_s"] for r in use for d in r["delays"] if d["target"] == "soiled" and d["delay_s"] is not None]
    missed = sum(d["delay_s"] is None for r in use for d in r["delays"])
    mv = [r for r in use if r["protocol"] == "movement"]
    mv_min = sum(r["moving_minutes"] for r in mv)
    return {
        "runs": len(use), "only_examples": only_examples,
        "accuracy": round(float(np.mean(accs)), 3) if accs else None,
        "wet_delay_s": round(float(np.median(wet_d))) if wet_d else None,
        "poo_delay_s": round(float(np.median(poo_d))) if poo_d else None,
        "missed_events": missed,
        "movement_runs": len(mv), "movement_minutes": mv_min,
        "false_alarms_movement": sum(r["false_alarms"] for r in mv),
        "wees_detected": sum(r["wees_detected"] for r in use if r["protocol"] == "insult"),
        "insults": sum(r["insults"] for r in use if r["protocol"] == "insult"),
    }


# ----------------------------------------------------------------------------- Product Lab
def lab_metrics(run, P):
    """Product-performance metrics from an insult run."""
    rel, sat = P["rel_s"].to_numpy(), P["sat"].to_numpy()
    hum = P["hum"].to_numpy(dtype=float)
    ev = P["event"].to_numpy()
    ins = [i for i in range(len(ev)) if str(ev[i]).startswith("insult")]
    if not ins:
        return None
    t0 = rel[ins[0]]
    bounds = ins + [len(rel)]
    press = [i for i in range(len(ev)) if ev[i] == "press_start"]
    if press:
        bounds[-1] = press[0]
    per = []

    def med(a, lo, hi):
        seg = a[lo:hi]
        seg = seg[np.isfinite(seg)]
        return float(np.median(seg)) if len(seg) else None

    for k, i in enumerate(ins):
        j = bounds[k + 1]
        pre = med(sat, max(0, i - 20), i) or 0.0
        w_end = min(j, i + 120)
        pk_i = i + int(np.argmax(sat[i:w_end])) if w_end > i else i
        peak = float(sat[pk_i])
        plateau = med(sat, max(i, j - 60), j)
        rise_target = pre + 0.9 * (peak - pre)
        rise_i = next((x for x in range(i, w_end) if sat[x] >= rise_target), pk_i)
        overshoot = peak - (plateau or peak)
        absorb = None
        if overshoot > 0.05:
            lvl = plateau + 0.25 * overshoot
            ab_i = next((x for x in range(pk_i, j) if sat[x] <= lvl), None)
            absorb = None if ab_i is None else float(rel[ab_i] - rel[i])
        h_pre = med(hum, max(0, i - 20), i)
        h_5 = med(hum, min(j - 1, i + 285), min(j, i + 315))
        per.append({
            "n": k + 1, "volume_ml": float(run["df"]["volume_ml"].iloc[i]) if "volume_ml" in run["df"] else None,
            "pre": round(pre, 3), "peak": round(peak, 3), "plateau": None if plateau is None else round(plateau, 3),
            "rise_s": round(float(rel[rise_i] - rel[i]), 1), "absorb_s": None if absorb is None else round(absorb),
            "step": None if plateau is None else round(plateau - pre, 3),
            "hum_rise": None if (h_pre is None or h_5 is None) else round(h_5 - h_pre, 1),
        })
    full_at = next((p["n"] for p in per if (p["plateau"] or 0) >= C.LEAK_SAT), None)
    rewet = None
    if press:
        i = press[0]
        end = next((x for x in range(i, len(ev)) if ev[x] == "press_end"), min(len(ev), i + 30))
        before = med(sat, max(0, i - 30), i)
        rewet = None if before is None else round(float(np.max(sat[i:end + 1])) - before, 3)
    after = rel >= t0
    humid_min = float(np.sum((hum > 80) & after)) / 60 if np.isfinite(hum).any() else None
    step = max(1, int(after.sum() // 300))
    return {
        "id": run["id"], "nappy": run["nappy"], "placement": run["placement"], "source": run["source"],
        "insults": per, "full_at_insult": full_at, "rewet": rewet,
        "humid_minutes": None if humid_min is None else round(humid_min, 1),
        "series": {"min": [round(float(x - t0) / 60, 2) for x in rel[after][::step]],
                   "sat": [round(float(x), 3) for x in sat[after][::step]],
                   "hum": [None if not np.isfinite(x) else round(float(x), 1) for x in hum[after][::step]],
                   "insult_min": [round(float(rel[i] - t0) / 60, 2) for i in ins]},
    }


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return float(np.mean(xs)) if xs else None


def lab_findings(labs):
    """Plain-English comparisons between the products tested."""
    by = {}
    for L in labs:
        by.setdefault(L["nappy"], []).append(L)
    if len(by) < 2:
        return []
    rows = []
    for name, Ls in by.items():
        rows.append({"nappy": name,
                     "absorb": _mean([p["absorb_s"] for L in Ls for p in L["insults"] if p["n"] >= 2]),
                     "full": _mean([L["full_at_insult"] for L in Ls]),
                     "rewet": _mean([L["rewet"] for L in Ls]),
                     "humid": _mean([L["humid_minutes"] for L in Ls])})
    out = []
    a = [r for r in rows if r["absorb"]]
    if len(a) >= 2:
        a.sort(key=lambda r: r["absorb"])
        out.append(f"{a[0]['nappy']} absorbs repeat insults {a[-1]['absorb'] / a[0]['absorb']:.1f}× faster than "
                   f"{a[-1]['nappy']} ({a[0]['absorb']:.0f} s vs {a[-1]['absorb']:.0f} s to soak away the surface fluid).")
    f_ = [r for r in rows if r["full"]]
    if f_:
        f_.sort(key=lambda r: r["full"])
        first = f_[0]
        others = [r["nappy"] for r in rows if r["nappy"] != first["nappy"]]
        out.append(f"{first['nappy']} reached the full point after insult {first['full']:.0f}"
                   + (f"; {', '.join(others)} held more." if len(f_) == 1 else "."))
    r_ = [r for r in rows if r["rewet"] is not None]
    if len(r_) >= 2 and max(x["rewet"] for x in r_) - min(x["rewet"] for x in r_) >= 0.03:
        r_.sort(key=lambda r: r["rewet"])
        out.append(f"Under pressure, {r_[-1]['nappy']} pushed more fluid back to the surface (rewet "
                   f"{r_[-1]['rewet'] * 100:.0f} vs {r_[0]['rewet'] * 100:.0f} points), so skin stays wetter.")
    h_ = [r for r in rows if r["humid"] is not None]
    if len(h_) >= 2:
        h_.sort(key=lambda r: r["humid"])
        if h_[-1]["humid"] - h_[0]["humid"] >= 2:
            out.append(f"Inside {h_[-1]['nappy']}, humidity stayed above 80% for {h_[-1]['humid']:.0f} min vs "
                       f"{h_[0]['humid']:.0f} min, which points to lower breathability.")
    return out


def analyse(patterns=("bench/*.csv", "bench/examples/*.csv"), models_path="models.joblib"):
    """Everything the Lab page needs."""
    if not os.path.exists(models_path):
        return {"error": f"{models_path} not found - run python train.py first"}
    M, cal = joblib.load(models_path), load_cal()
    runs = load_runs(patterns)
    results, labs = [], []
    for r in runs:
        P = predict(r, M, cal)
        results.append(validate_run(r, P))
        if r["protocol"] == "insult":
            L = lab_metrics(r, P)
            if L:
                labs.append(L)
    real_labs = [L for L in labs if L["source"] == "bench"]
    show = real_labs or labs
    return {"validation": summarize_validation(results), "runs": results, "labs": show,
            "labs_only_examples": not real_labs and bool(labs), "findings": lab_findings(show)}

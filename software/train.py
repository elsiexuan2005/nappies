"""Train the three models on synthetic data and save models.joblib.

  state model : dry / wet / soiled            (Random Forest)
  activity    : still / moving                (small decision tree - can go on-device)
  leak model  : P(diaper full within 60 min)  (gradient boosting classifier)  <- main signal
  ttl model   : rough minutes until full      (gradient boosting regressor)   <- display only

Sessions are split train/test by session, so test data is never seen in training.

  python train.py --sessions 300
  python train.py --sessions 300 --bench "bench/*.csv"   # also learn from real labelled bench runs
"""
import argparse

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor, RandomForestClassifier
from sklearn.metrics import classification_report, confusion_matrix, mean_absolute_error, roc_auc_score
from sklearn.tree import DecisionTreeClassifier, export_text

import config as C
from features import FEATURES_ACT, FEATURES_LEAK, FEATURES_STATE, FEATURES_TTL, compute_features
from sensor import load_cal
from simulate import simulate_session


def build_dataset(n, seed, cal):
    rng = np.random.default_rng(seed)
    parts = []
    for k in range(n):
        df, meta = simulate_session(rng, cal)
        f = compute_features(df, cal, meta["size"], step=C.FEATURE_STEP_S).reset_index(drop=True)
        lab = df.iloc[::C.FEATURE_STEP_S][["state", "moving", "sat_true", "ttl_min"]].reset_index(drop=True)
        f = pd.concat([f, lab], axis=1)
        f["session"], f["age"], f["kind"] = k, meta["age"], meta["kind"]
        parts.append(f)
        if (k + 1) % 50 == 0:
            print(f"  simulated {k + 1}/{n} sessions")
    return pd.concat(parts, ignore_index=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", type=int, default=300)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--cal", default="calibration.json")
    ap.add_argument("--out", default="models.joblib")
    ap.add_argument("--bench", default=None, help='glob of labelled bench runs to add to state training, e.g. "bench/*.csv"')
    ap.add_argument("--bench-weight", type=float, default=5.0)
    a = ap.parse_args()

    cal = load_cal(a.cal)
    print(f"Simulating {a.sessions} diaper sessions (day + overnight) ...")
    data = build_dataset(a.sessions, a.seed, cal)
    data.to_csv("training_data.csv", index=False)
    print(f"{len(data):,} feature rows  |  class mix:",
          data["state"].map(dict(enumerate(C.STATES))).value_counts().to_dict())

    test_mask = data["session"] >= int(a.sessions * 0.8)
    tr, te = data[~test_mask], data[test_mask]
    report = []

    # 1. diaper state (optionally with real bench rows, weighted up)
    state = RandomForestClassifier(n_estimators=150, max_depth=12, min_samples_leaf=5,
                                   class_weight="balanced", n_jobs=-1, random_state=0)
    Xs, ys, ws = tr[FEATURES_STATE], tr["state"], np.ones(len(tr))
    if a.bench:
        import benchlib as B
        rows = []
        for r in B.load_runs([a.bench]):
            if r["source"] != "bench":
                continue
            df = r["df"]
            fb = compute_features(df, cal, 3, step=1).reset_index(drop=True)
            truth = df["truth"].map({s_: i for i, s_ in enumerate(C.STATES)}).to_numpy()
            rel = df["t"].to_numpy() - df["t"].iloc[0]
            chg = [rel[0]] + [rel[i] for i in range(1, len(truth)) if truth[i] != truth[i - 1]]
            since = np.array([t - max(c for c in chg if c <= t) for t in rel])
            keep = (since >= B.GRACE_S) & (np.arange(len(df)) % 5 == 0)
            fb = fb[keep]
            fb["state"] = truth[keep]
            rows.append(fb)
        if rows:
            bench_df = pd.concat(rows, ignore_index=True)
            Xs = pd.concat([Xs, bench_df[FEATURES_STATE]], ignore_index=True)
            ys = pd.concat([ys, bench_df["state"]], ignore_index=True)
            ws = np.concatenate([ws, np.full(len(bench_df), a.bench_weight)])
            report += [f"Added {len(bench_df)} labelled bench rows to state training (weight {a.bench_weight:g})", ""]
        else:
            report += ["No real bench runs found for --bench (simulated examples are ignored)", ""]
    state.fit(Xs, ys, sample_weight=ws)
    p = state.predict(te[FEATURES_STATE])
    r = classification_report(te["state"], p, target_names=C.STATES, digits=3)
    cm = confusion_matrix(te["state"], p)
    imp = sorted(zip(FEATURES_STATE, state.feature_importances_), key=lambda x: -x[1])
    report += ["== Diaper state (dry/wet/soiled) ==", r, "Confusion matrix (rows=true):", str(cm),
               "Feature importance: " + ", ".join(f"{n}={v:.2f}" for n, v in imp), ""]

    # 2. activity
    act = DecisionTreeClassifier(max_depth=2, min_samples_leaf=200, class_weight="balanced", random_state=0)
    act.fit(tr[FEATURES_ACT], tr["moving"])
    p = act.predict(te[FEATURES_ACT])
    report += ["== Activity (still/moving) ==",
               classification_report(te["moving"], p, target_names=["still", "moving"], digits=3),
               "Tree rules (portable to firmware):", export_text(act, feature_names=FEATURES_ACT), ""]

    # 3. leak risk within the next hour (classification - the honest framing,
    #    because the exact minute depends on when the next wee happens)
    report += [f"Sessions reaching leak risk: {data.groupby('session')['ttl_min'].min().eq(0).mean():.0%}"]
    y_tr, y_te = (tr["ttl_min"] < 60).astype(int), (te["ttl_min"] < 60).astype(int)
    leak = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.08, class_weight="balanced", random_state=0)
    leak.fit(tr[FEATURES_LEAK], y_tr)
    pl = leak.predict_proba(te[FEATURES_LEAK])[:, 1]
    before = (te["ttl_min"] > 0).to_numpy()          # exclude rows already past the leak point
    auc_all = roc_auc_score(y_te, pl)
    auc_before = roc_auc_score(y_te[before], pl[before])
    report += ["== Leak risk: full within next 60 min ==",
               f"ROC AUC: {auc_all:.3f} (all rows) | {auc_before:.3f} (only rows before the diaper is full)",
               classification_report(y_te[before], pl[before] > 0.5, target_names=["ok", "full <60min"], digits=3)]

    # 4. rough time to full (log target, weighted towards diapers that do fill up)
    trr, ter = tr[tr["ttl_min"] > 0], te[te["ttl_min"] > 0]
    ttl = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.08, random_state=0)
    ttl.fit(trr[FEATURES_TTL], np.log1p(trr["ttl_min"]),
            sample_weight=np.where(trr["ttl_min"] < C.TTL_CAP_MIN, 5.0, 1.0))
    pr = np.clip(np.expm1(ttl.predict(ter[FEATURES_TTL])), 0, C.TTL_CAP_MIN)
    within = (ter["ttl_min"] < C.TTL_CAP_MIN).to_numpy()
    report += ["== Rough time to full (display only) ==",
               f"MAE: {mean_absolute_error(ter['ttl_min'], pr):.0f} min overall | "
               f"{mean_absolute_error(ter['ttl_min'][within], pr[within]):.0f} min when full within 4 h "
               "(error is dominated by the random timing of the next wee)", ""]

    text = "\n".join(report)
    print("\n" + text)
    with open("models_report.txt", "w") as f:
        f.write(text)

    joblib.dump({"state": state, "act": act, "leak": leak, "ttl": ttl,
                 "features": {"state": FEATURES_STATE, "act": FEATURES_ACT, "leak": FEATURES_LEAK, "ttl": FEATURES_TTL},
                 "states": C.STATES}, a.out)
    print(f"saved {a.out} and models_report.txt")


if __name__ == "__main__":
    main()

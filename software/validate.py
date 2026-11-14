"""Check the models against labelled bench runs (P&G: "test against a labelled bench dataset").

  python validate.py                    # all runs in bench/ (+ examples if no real runs)
  python validate.py bench/my_run.csv   # specific runs

Writes validation_report.md and a plot per run in bench/plots/.
For each run it reports:
  accuracy          share of rows where the predicted state (dry/wet/soiled) matches the truth,
                    ignoring the first 30 s after each change
  detection delay   seconds from pouring fluid (or placing the poo proxy) to a sustained correct state
  false alarms      sustained predictions worse than the truth - movement runs test this
  wees detected     vs the number of insults actually poured
"""
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import benchlib as B  # noqa: E402

COLORS = {"dry": "#7DB58F", "wet": "#3E8E9B", "soiled": "#A0745A"}


def plot(run, P, path):
    fig, ax = plt.subplots(3, 1, figsize=(10, 6.2), sharex=True, gridspec_kw={"height_ratios": [3, 2, 1.2]})
    m = P["rel_s"] / 60
    ax[0].plot(m, P["sat"], color="#1D2547", lw=1.6, label="Moisture saturation")
    if P["hum"].notna().any():
        a2 = ax[0].twinx()
        a2.plot(m, P["hum"], color="#F2A65A", lw=1.2, label="Humidity %")
        a2.set_ylabel("Humidity %", color="#F2A65A")
    ax[0].set_ylabel("Saturation")
    ax[0].set_ylim(-0.02, 1.05)
    ax[1].plot(m, P["gas_rise"], color="#5D6584", lw=1.2)
    ax[1].set_ylabel("Gas rise")
    for i, e in enumerate(P["event"]):
        if e:
            for a in ax:
                a.axvline(m.iloc[i], color="#E0605E", ls="--", lw=0.9)
            ax[0].text(m.iloc[i], 1.02, e, fontsize=7, color="#E0605E", rotation=0)
    for row, key in ((0.6, "truth"), (0.0, "pred")):
        for s, c in COLORS.items():
            mask = (P[key] == s).to_numpy()
            ax[2].fill_between(m, row, row + 0.35, where=mask, color=c, step="mid", lw=0)
    ax[2].set_yticks([0.17, 0.77], ["model", "truth"])
    ax[2].set_xlabel("Minutes")
    ax[0].set_title(f"{run['nappy']}  |  {run['protocol']}  |  {run['placement']}  |  {run['source']}", fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def main(paths):
    pats = paths or ["bench/*.csv"]
    runs = B.load_runs(pats)
    examples = False
    if not runs and not paths:
        runs = B.load_runs(["bench/examples/*.csv"])
        examples = True
    if not runs:
        sys.exit("No bench runs found. Record one with: python bench.py insult --nappy \"...\" --port ...")
    import joblib
    M, cal = joblib.load("models.joblib"), B.load_cal()
    os.makedirs("bench/plots", exist_ok=True)
    results = []
    for r in runs:
        P = B.predict(r, M, cal)
        res = B.validate_run(r, P)
        results.append(res)
        plot(r, P, f"bench/plots/{r['id']}.png")
    S = B.summarize_validation(results)

    lines = ["# Bench validation report", ""]
    if examples:
        lines += ["**These are simulated example runs, not real bench data.** Record real runs with `bench.py`.", ""]
    lines += [f"Runs: {S['runs']}", "",
              f"- Accuracy (dry/wet/soiled, after 30 s settling): **{S['accuracy'] * 100:.1f}%**" if S.get("accuracy") is not None else "- Accuracy: n/a",
              f"- Median time to detect wet: **{S['wet_delay_s']} s**" if S.get("wet_delay_s") is not None else "- Time to detect wet: n/a",
              f"- Median time to detect poo: **{S['poo_delay_s']} s**" if S.get("poo_delay_s") is not None else "- Time to detect poo: n/a",
              f"- Missed events: {S['missed_events']}",
              f"- False alarms during {S['movement_minutes']:.0f} min of movement-only testing: **{S['false_alarms_movement']}**",
              f"- Wees detected: {S['wees_detected']} of {S['insults']} insults", "",
              "| Run | Protocol | Placement | Accuracy | Detection delay | False alarms | Wees detected |",
              "|---|---|---|---|---|---|---|"]
    for r in results:
        d = ", ".join(f"{x['event']}: {x['delay_s']:.0f} s" if x["delay_s"] is not None else f"{x['event']}: missed"
                      for x in r["delays"]) or "–"
        acc = f"{r['accuracy'] * 100:.1f}%" if r["accuracy"] is not None else "–"
        lines.append(f"| {r['nappy']} | {r['protocol']} | {r['placement']} | {acc} | {d} | {r['false_alarms']} | "
                     f"{r['wees_detected']} / {r['insults']} |")
    lines += ["", "Plots: `bench/plots/`"]
    text = "\n".join(lines)
    with open("validation_report.md", "w") as f:
        f.write(text + "\n")
    print(text)


if __name__ == "__main__":
    main(sys.argv[1:])

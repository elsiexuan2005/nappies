"""Business view: simulate a fleet of connected pods and turn it into P&G insights.

Every baby is simulated twice with the same random draws:
  without pod - parents size up late, notice poos by smell, no overnight alerts
  with pod    - size-up flagged from fit/leak data, poo + overnight-full alerts
so the comparison isolates the effect of the pod.

All numbers come from ASSUMPTIONS below + config.py. They are a projection to
show what the data would tell P&G, not measured results.

  python fleet.py            # prints the summary
"""
import json

import numpy as np

import config as C

# ASSUMPTION - upper weight limit (kg) per size; check the real size chart
SIZE_MAX_KG = {1: 5, 2: 8, 3: 10, 4: 14, 5: 16}
# ASSUMPTION - diapers used per day by age group
DIAPERS_PER_DAY = {"0-3m": 9.0, "3-6m": 7.5, "6-12m": 6.0}
# ASSUMPTION - typical overnight capacity (mL) per size, before leak risk
NIGHT_CAPACITY_ML = {1: 140, 2: 190, 3: 240, 4: 300, 5: 350}
NIGHT_H = 10.5
POD_ALERT_RESPONSE = 0.75     # ASSUMPTION - share of overnight "nearly full" alerts parents act on
POD_SIZEUP_FIT = 0.90         # pod flags size-up at 90 % of the size's weight limit


def _group(age_d):
    return np.where(age_d < 90, "0-3m", np.where(age_d < 180, "3-6m", "6-12m"))


def _weight(age_d, pct):
    base = np.where(age_d < 120, 3.4 + 0.028 * age_d, 3.4 + 0.028 * 120 + 0.014 * (age_d - 120))
    return base * pct


def _first_size(w):
    out = np.full(w.shape, 5)
    for s in sorted(SIZE_MAX_KG, reverse=True):
        out = np.where(w <= 0.85 * SIZE_MAX_KG[s], s, out)
    return out


def simulate_fleet(n=3000, history_days=56, forecast_days=28, seed=3):
    rng = np.random.default_rng(seed)
    days = history_days + forecast_days
    start_age = rng.uniform(0, 300, n)
    pct = rng.lognormal(0, 0.08, n)
    delay = rng.uniform(3, 14, n)                 # days parents wait after outgrowing a size
    size = {"ctrl": _first_size(_weight(start_age, pct)), "pod": None}
    size["pod"] = size["ctrl"].copy()
    over_days = {"ctrl": np.zeros(n), "pod": np.zeros(n)}
    smax = np.vectorize(SIZE_MAX_KG.get)

    rec = {k: {"leaks": np.zeros((n, days)), "fit": np.zeros((n, days)), "fill": np.zeros((n, days)),
               "size": np.zeros((n, days), int), "soiled_min": np.zeros((n, days))} for k in ("ctrl", "pod")}
    diapers = np.zeros((n, days))
    age_days = np.zeros((n, days))

    for d in range(days):
        age = start_age + d
        age_days[:, d] = age
        w = _weight(age, pct)
        grp = _group(age)
        rate = np.vectorize(DIAPERS_PER_DAY.get)(grp)
        diapers[:, d] = rng.poisson(rate)
        # shared random draws (common random numbers)
        interval = np.vectorize(lambda x: C.AGE_GROUPS[x]["void_interval_min"])(grp)
        mean_ml = np.vectorize(lambda x: C.AGE_GROUPS[x]["void_ml"][0])(grp)
        n_night = rng.poisson(NIGHT_H * 60 / (interval * C.SLEEP_VOID_INTERVAL_FACTOR))
        night_ml = n_night * mean_ml * rng.lognormal(0, 0.25, n)
        poos = rng.poisson(np.vectorize(lambda x: C.AGE_GROUPS[x]["poo_per_day"])(grp))
        u_vol, u_fit_n, u_resp = rng.random(n), rng.random(n), rng.random(n)
        day_u = rng.random(n)
        notice_ctrl = rng.uniform(5, 40, n)
        notice_pod = rng.uniform(3, 12, n)

        for k in ("ctrl", "pod"):
            sz = size[k]
            fit = w / smax(sz)
            fill = night_ml / np.vectorize(NIGHT_CAPACITY_ML.get)(sz)
            fit_risk = np.clip((fit - 0.85) / 0.25, 0, 1)
            vol_leak = (fill > 1.0) & (u_vol < 0.6)
            if k == "pod":
                vol_leak &= ~((fill > 0.85) & (u_resp < POD_ALERT_RESPONSE))   # alerted and changed
            fit_leak = u_fit_n < 0.02 + 0.25 * fit_risk
            day_leaks = (day_u < 1 - (1 - (0.004 + 0.04 * fit_risk)) ** diapers[:, d]).astype(float)
            r = rec[k]
            r["leaks"][:, d] = vol_leak.astype(float) + fit_leak.astype(float) + day_leaks
            r["fit"][:, d], r["fill"][:, d], r["size"][:, d] = fit, fill, sz
            r["soiled_min"][:, d] = poos * (notice_pod if k == "pod" else notice_ctrl)
            # size-up policy for tomorrow
            if k == "ctrl":
                over_days[k] = np.where(fit > 1.0, over_days[k] + 1, 0)
                up = over_days[k] >= delay
            else:
                over_days[k] = np.where(fit > POD_SIZEUP_FIT, over_days[k] + 1, 0)
                up = over_days[k] >= 2
            size[k] = np.where(up & (sz < 5), sz + 1, sz)
            over_days[k] = np.where(up, 0, over_days[k])

    H = history_days
    return {"rec": rec, "diapers": diapers, "age": age_days, "H": H, "n": n, "days": days}


def summarize(sim):
    rec, dia, H, n = sim["rec"], sim["diapers"], sim["H"], sim["n"]
    c, p = rec["ctrl"], rec["pod"]
    hist = slice(0, H)
    dh = dia[:, hist].sum()
    months = H / 30.4

    def per1000(r):
        return float(r["leaks"][:, hist].sum() / dh * 1000)

    # leak rate vs fit (without pod - the "natural" relationship)
    bins = np.arange(0.55, 1.21, 0.05)
    fit, lk, dd = c["fit"][:, hist].ravel(), c["leaks"][:, hist].ravel(), dia[:, hist].ravel()
    idx = np.digitize(fit, bins)
    fit_curve = []
    for i in range(1, len(bins)):
        m = idx == i
        if dd[m].sum() > 500:
            fit_curve.append({"fit": round(float(bins[i - 1] + 0.025) * 100), "leaks_per_100": round(float(lk[m].sum() / dd[m].sum() * 100), 2)})
    low = np.mean([x["leaks_per_100"] for x in fit_curve if x["fit"] < 80])
    high = np.mean([x["leaks_per_100"] for x in fit_curve if x["fit"] >= 95])

    # overnight fill by size
    night = []
    for s in sorted(SIZE_MAX_KG):
        m = c["size"][:, hist] == s
        if m.sum() > 200:
            f = c["fill"][:, hist][m]
            night.append({"size": s, "pct_over_85": round(float((f > 0.85).mean() * 100), 1),
                          "median_fill": round(float(np.median(f) * 100))})
    worst = max(night, key=lambda x: x["pct_over_85"])

    # forecast: diapers needed per week per size (pod arm, projected forward)
    weeks = []
    for wk in range(4):
        sl = slice(H + 7 * wk, H + 7 * (wk + 1))
        row = {"week": f"Week {wk + 1}"}
        for s in sorted(SIZE_MAX_KG):
            row[f"s{s}"] = int(dia[:, sl][p["size"][:, sl] == s].sum())
        weeks.append(row)
    sizeup_14 = int(((p["size"][:, H + 13] > p["size"][:, H - 1])).sum())
    mix_now = {s: int((p["size"][:, H - 1] == s).sum()) for s in SIZE_MAX_KG}

    # pod vs control per baby per month
    too_small = lambda r: float((r["fit"][:, hist] > 1.0).sum() / n / months)   # noqa: E731
    compare = [
        {"metric": "Leaks per baby per month", "without": round(float(c["leaks"][:, hist].sum() / n / months), 2),
         "with": round(float(p["leaks"][:, hist].sum() / n / months), 2)},
        {"metric": "Days per month in a too-small size", "without": round(too_small(c), 2), "with": round(too_small(p), 2)},
        {"metric": "Minutes per day in a soiled diaper", "without": round(float(c["soiled_min"][:, hist].mean()), 1),
         "with": round(float(p["soiled_min"][:, hist].mean()), 1)},
    ]
    leak_cut = 1 - compare[0]["with"] / compare[0]["without"]

    return {
        "fleet": {"pods": n, "weeks": round(H / 7), "diapers_tracked": int(dh),
                  "diapers_per_day": round(float(dia[:, hist].mean()), 1)},
        "kpis": {
            "leaks_per_1000_without": round(per1000(c), 1), "leaks_per_1000_with": round(per1000(p), 1),
            "leak_reduction_pct": round(leak_cut * 100), "sizeup_next_14d": sizeup_14,
            "forecast_4w": int(sum(sum(v for k, v in w.items() if k != "week") for w in weeks)),
            "avg_night_fill_pct": round(float(np.median(c["fill"][:, hist]) * 100)),
        },
        "fit_curve": fit_curve, "fit_multiplier": round(float(high / low), 1),
        "night_fill": night, "night_worst": worst,
        "forecast": weeks, "size_mix_now": mix_now,
        "compare": compare,
        "assumptions": [
            "Fleet is simulated, not measured. Babies 0–10 months, 8 weeks of history.",
            "Size weight limits (kg): " + ", ".join(f"S{k} ≤{v}" for k, v in SIZE_MAX_KG.items()) + ". Check the real size chart.",
            "Diapers per day: " + ", ".join(f"{k} {v:g}" for k, v in DIAPERS_PER_DAY.items()) + ".",
            "Without pod, parents size up 3–14 days after the baby passes the weight limit. With pod, size-up is flagged at 90%.",
            f"Parents act on {POD_ALERT_RESPONSE:.0%} of overnight 'nearly full' alerts.",
            "Poos are noticed after 5–40 min without the pod, 3–12 min with an alert.",
        ],
    }


def build(n=3000):
    return summarize(simulate_fleet(n=n))


if __name__ == "__main__":
    s = build()
    print(json.dumps({k: s[k] for k in ("fleet", "kpis", "fit_multiplier", "night_worst", "compare")}, indent=2))

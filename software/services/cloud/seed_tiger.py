"""Seed synthetic multi-pod summaries into the cloud store (Tiger Cloud or SQLite).

Gives the business dashboard a 48h continuous-aggregate history without a real fleet.

  DATABASE_URL=postgres://... python services/cloud/seed_tiger.py
  python services/cloud/seed_tiger.py                    # SQLite under CLOUD_DATA
  python services/cloud/seed_tiger.py --devices 40 --hours 48 --every 10

env: DATABASE_URL  CLOUD_DATA=cloud_data  RETENTION_DAYS=365
"""
from __future__ import annotations

import argparse
import hashlib
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from services.cloud.db import SummaryStore  # noqa: E402
from services.common import ROOT, env, log  # noqa: E402

os.chdir(ROOT)
L = log("seed-tiger")


def fake_device(i: int) -> str:
    return hashlib.sha256(f"seed-pod-{i}".encode()).hexdigest()[:16]


def make_summary(device: str, ts: float, rng: random.Random) -> dict:
    size = rng.choice([1, 2, 3, 4, 5])
    healthy = max(0.35, min(0.98, rng.gauss(0.78, 0.08)))
    soiled = max(0.0, rng.gauss(18, 8))
    humid = max(0.0, rng.gauss(25, 10))
    wees = max(0, int(rng.gauss(5, 1.5)))
    rash = rng.choices(["low", "medium", "high"], weights=[0.7, 0.25, 0.05])[0]
    return {
        "device": device,
        "ts": ts,
        "age_band": rng.choice(["0-3m", "3-6m", "6-10m"]),
        "size": size,
        "dht_mode": "nappy",
        "events": {"wee": rng.randint(0, 2), "poo": 1 if rng.random() < 0.08 else 0},
        "today": {
            "wees": wees,
            "poos": rng.randint(0, 3),
            "changes": max(1, wees // 2),
            "healthy_pct": round(healthy, 3),
            "soiled_min": round(soiled, 1),
            "overfull_min": round(max(0.0, rng.gauss(8, 4)), 1),
            "humid_min": round(humid, 1),
            "avg_diaper_min": round(rng.gauss(180, 40), 1),
        },
        "levels": {"rash": rash, "hydration": rng.choice(["ok", "low", "high"]),
                   "gut": rng.choice(["ok", "irregular"])},
        "sleep": {
            "sleep_min": round(rng.gauss(500, 60), 1),
            "active_min": round(rng.gauss(120, 30), 1),
            "wakeups": rng.randint(1, 6),
            "wet_wakeups": rng.randint(0, 3),
        },
        "now": {
            "state": rng.choice(["dry", "wet", "soiled"]),
            "sat": round(rng.uniform(0.05, 0.95), 3),
            "p_full_1h": round(rng.uniform(0.05, 0.85), 3),
        },
        "climate": {"temp": round(rng.gauss(33, 1.5), 1), "hum": round(rng.gauss(70, 10), 1)},
    }


def main():
    ap = argparse.ArgumentParser(description="Seed fleet summaries for Tiger / SQLite ingest demo")
    ap.add_argument("--devices", type=int, default=40)
    ap.add_argument("--hours", type=int, default=48)
    ap.add_argument("--every", type=int, default=10, help="minutes between summaries per device")
    ap.add_argument("--batch", type=int, default=500)
    args = ap.parse_args()

    store = SummaryStore(env("CLOUD_DATA", "cloud_data"))
    L.info("backend=%s devices=%d hours=%d every=%d min",
           store.backend, args.devices, args.hours, args.every)

    now = time.time()
    step = args.every * 60
    n_steps = max(1, (args.hours * 3600) // step)
    devices = [fake_device(i) for i in range(args.devices)]
    rng = random.Random(42)

    batch, total = [], 0
    for step_i in range(n_steps):
        ts = now - (n_steps - 1 - step_i) * step
        for d in devices:
            batch.append(make_summary(d, ts, rng))
            if len(batch) >= args.batch:
                total += store.insert_summaries(batch)
                batch = []
                L.info("inserted %d …", total)
    if batch:
        total += store.insert_summaries(batch)

    if store.backend == "tiger":
        L.info("refreshing continuous aggregate summaries_hourly …")
        store.refresh_continuous_aggregate()

    agg = store.aggregate()
    L.info("done: stored=%d devices_24h=%s summaries_total=%s hourly_buckets=%d backend=%s",
           total, agg.get("devices_24h"), agg.get("summaries_total"),
           len(agg.get("hourly") or []), agg.get("backend"))
    store.close()


if __name__ == "__main__":
    main()

"""CLOUD · ingest: the single entry point for data coming from edge hubs and R&D benches.

  POST /v1/summaries         {"summaries": [...]}  anonymised 1-min summaries from cloud-sync   (X-Api-Key)
  POST /v1/bench-runs?name=  raw CSV from bench.py / upload_bench.py (R&D lab, no personal data) (X-Api-Key)
  GET  /v1/aggregate         fleet roll-up for the business service
  GET  /v1/bench-runs        list of uploaded bench runs

Storage: Tiger Cloud (TimescaleDB) when DATABASE_URL is set; otherwise SQLite under CLOUD_DATA.
Bench CSVs go to BENCH_DIR (shared volume here; object storage such as S3/Blob in production).

env: PORT=9001  API_KEY=dev-key  CLOUD_DATA=cloud_data  BENCH_DIR=cloud_data/bench
     RETENTION_DAYS=365  DATABASE_URL=postgres://...  (optional Tiger Cloud)
"""
import json
import os
import re
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from services.cloud.db import SummaryStore  # noqa: E402
from services.common import ROOT, env, forever, log, serve  # noqa: E402

os.chdir(ROOT)
L = log("ingest")
DATA = env("CLOUD_DATA", "cloud_data")
BENCH = env("BENCH_DIR", os.path.join(DATA, "bench"))
os.makedirs(DATA, exist_ok=True)
os.makedirs(BENCH, exist_ok=True)
store = SummaryStore(DATA)
L.info("summaries backend: %s", store.backend)


def post_summaries(h, body):
    items = json.loads(body).get("summaries", [])
    n = store.insert_summaries(items)
    return 200, {"stored": n, "backend": store.backend}


def post_bench(h, body):
    q = h.path.split("?", 1)[1] if "?" in h.path else ""
    name = dict(p.split("=", 1) for p in q.split("&") if "=" in p).get("name", f"run_{int(time.time())}")
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", name)
    if not name.endswith(".csv"):
        name += ".csv"
    head = body[:400].decode(errors="ignore")
    if "truth" not in head or "moisture" not in head:
        return 400, {"error": "not a bench.py CSV"}
    with open(os.path.join(BENCH, name), "wb") as f:
        f.write(body)
    L.info("bench run stored: %s (%d bytes)", name, len(body))
    return 200, {"stored": name}


def get_aggregate(h, _):
    return 200, store.aggregate()


def get_bench(h, _):
    return 200, {"runs": sorted(f for f in os.listdir(BENCH) if f.endswith(".csv"))}


def retention():
    days = env("RETENTION_DAYS", "365", int)
    while True:
        if not store.uses_tiger_retention:
            store.run_retention(days)
        time.sleep(3600)


if __name__ == "__main__":
    threading.Thread(target=retention, daemon=True).start()
    serve(env("PORT", "9001", int),
          {"/v1/aggregate": get_aggregate, "/v1/bench-runs": get_bench},
          {"/v1/summaries": post_summaries, "/v1/bench-runs": post_bench},
          service="ingest", api_key=env("API_KEY", "dev-key"))
    forever()

"""CLOUD · ingest: the single entry point for data coming from edge hubs and R&D benches.

  POST /v1/summaries         {"summaries": [...]}  anonymised 1-min summaries from cloud-sync   (X-Api-Key)
  POST /v1/bench-runs?name=  raw CSV from bench.py / upload_bench.py (R&D lab, no personal data) (X-Api-Key)
  GET  /v1/aggregate         fleet roll-up for the business service
  GET  /v1/bench-runs        list of uploaded bench runs

Storage: SQLite here; Postgres/managed DB in production (same schema). Bench CSVs go to
BENCH_DIR (a shared volume here; object storage such as S3/Blob in production).

env: PORT=9001  API_KEY=dev-key  CLOUD_DATA=cloud_data  BENCH_DIR=cloud_data/bench  RETENTION_DAYS=365
"""
import json
import os
import re
import sqlite3
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from services.common import ROOT, env, forever, log, serve  # noqa: E402

os.chdir(ROOT)
L = log("ingest")
DATA = env("CLOUD_DATA", "cloud_data")
BENCH = env("BENCH_DIR", os.path.join(DATA, "bench"))
os.makedirs(DATA, exist_ok=True)
os.makedirs(BENCH, exist_ok=True)
db = sqlite3.connect(os.path.join(DATA, "ingest.sqlite"), check_same_thread=False)
db.execute("""CREATE TABLE IF NOT EXISTS summaries (
    id INTEGER PRIMARY KEY, device TEXT, ts REAL, received REAL, body TEXT)""")
db.execute("CREATE INDEX IF NOT EXISTS ix_dev_ts ON summaries (device, ts)")
db.commit()
lock = threading.Lock()


def post_summaries(h, body):
    items = json.loads(body).get("summaries", [])
    now = time.time()
    with lock:
        db.executemany("INSERT INTO summaries (device, ts, received, body) VALUES (?,?,?,?)",
                       [(s.get("device"), s.get("ts"), now, json.dumps(s)) for s in items])
        db.commit()
    return 200, {"stored": len(items)}


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
    since = time.time() - 24 * 3600
    with lock:
        rows = db.execute("""SELECT s.body FROM summaries s JOIN (
                               SELECT device, MAX(id) AS id FROM summaries WHERE received > ? GROUP BY device
                             ) last ON s.id = last.id""", (since,)).fetchall()
        total = db.execute("SELECT COUNT(*) FROM summaries").fetchone()[0]
    latest = [json.loads(r[0]) for r in rows]

    def avg(key, sub="today"):
        v = [x[sub].get(key) for x in latest if x.get(sub) and x[sub].get(key) is not None]
        return round(sum(v) / len(v), 2) if v else None

    rash = {}
    for x in latest:
        lv = (x.get("levels") or {}).get("rash")
        rash[lv] = rash.get(lv, 0) + 1
    return 200, {"devices_24h": len(latest), "summaries_total": total,
                 "avg_healthy_pct": avg("healthy_pct"), "avg_soiled_min": avg("soiled_min"),
                 "avg_humid_min": avg("humid_min"), "avg_wees": avg("wees"), "rash_levels": rash}


def get_bench(h, _):
    return 200, {"runs": sorted(f for f in os.listdir(BENCH) if f.endswith(".csv"))}


def retention():
    days = env("RETENTION_DAYS", "365", int)
    while True:
        with lock:
            db.execute("DELETE FROM summaries WHERE received < ?", (time.time() - days * 86400,))
            db.commit()
        time.sleep(3600)


if __name__ == "__main__":
    threading.Thread(target=retention, daemon=True).start()
    serve(env("PORT", "9001", int),
          {"/v1/aggregate": get_aggregate, "/v1/bench-runs": get_bench},
          {"/v1/summaries": post_summaries, "/v1/bench-runs": post_bench},
          service="ingest", api_key=env("API_KEY", "dev-key"))
    forever()

"""Cloud summaries store: Tiger Cloud (TimescaleDB) when DATABASE_URL is set, else SQLite.

Tiger path uses a hypertable, retention policy, and a continuous aggregate for hourly
fleet roll-ups (MLH Best Use of Tiger Data). Edge spool stays SQLite regardless.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone


def _env(name, default=None):
    return os.environ.get(name, default)


def flatten_summary(s: dict, received: float | None = None) -> dict:
    """Turn a cloud-sync summary JSON into typed columns + full body."""
    today = s.get("today") or {}
    levels = s.get("levels") or {}
    now = s.get("now") or {}
    sleep = s.get("sleep") or {}
    climate = s.get("climate") or {}
    ts = s.get("ts")
    if ts is None:
        ts = time.time()
    size = s.get("size")
    try:
        size = int(size) if size is not None and str(size).strip() != "" else None
    except (TypeError, ValueError):
        size = None
    recv = received if received is not None else time.time()
    return {
        "ts": float(ts),
        "received": float(recv),
        "device": s.get("device") or "unknown",
        "age_band": s.get("age_band"),
        "size": size,
        "dht_mode": s.get("dht_mode"),
        "wees": today.get("wees"),
        "poos": today.get("poos"),
        "changes": today.get("changes"),
        "healthy_pct": today.get("healthy_pct"),
        "soiled_min": today.get("soiled_min"),
        "overfull_min": today.get("overfull_min"),
        "humid_min": today.get("humid_min"),
        "avg_diaper_min": today.get("avg_diaper_min"),
        "rash_level": levels.get("rash"),
        "hydration_level": levels.get("hydration"),
        "gut_level": levels.get("gut"),
        "sleep_min": sleep.get("sleep_min"),
        "active_min": sleep.get("active_min"),
        "wakeups": sleep.get("wakeups"),
        "wet_wakeups": sleep.get("wet_wakeups"),
        "now_state": now.get("state"),
        "sat": now.get("sat"),
        "p_full_1h": now.get("p_full_1h"),
        "temp": climate.get("temp"),
        "hum": climate.get("hum"),
        "events": s.get("events") or {},
        "body": s,
    }


def _avg(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def _ts_to_dt(epoch: float) -> datetime:
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc)


class SummaryStore:
    """Insert / aggregate / retention for anonymised pod summaries."""

    def __init__(self, data_dir: str | None = None):
        self.url = _env("DATABASE_URL")
        self.lock = threading.Lock()
        self._pg = None
        self._sqlite = None
        if self.url:
            self.backend = "tiger"
            self._init_tiger()
        else:
            self.backend = "sqlite"
            data_dir = data_dir or _env("CLOUD_DATA", "cloud_data")
            os.makedirs(data_dir, exist_ok=True)
            self._init_sqlite(os.path.join(data_dir, "ingest.sqlite"))

    @property
    def uses_tiger_retention(self) -> bool:
        return self.backend == "tiger"

    def _init_sqlite(self, path: str):
        self._sqlite = sqlite3.connect(path, check_same_thread=False)
        self._sqlite.execute("""CREATE TABLE IF NOT EXISTS summaries (
            id INTEGER PRIMARY KEY, device TEXT, ts REAL, received REAL, body TEXT)""")
        self._sqlite.execute("CREATE INDEX IF NOT EXISTS ix_dev_ts ON summaries (device, ts)")
        self._sqlite.commit()

    def _connect_pg(self):
        import psycopg
        return psycopg.connect(self.url, autocommit=True)

    def _init_tiger(self):
        import psycopg
        self._pg = self._connect_pg()
        with self._pg.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS summaries (
                    ts TIMESTAMPTZ NOT NULL,
                    received TIMESTAMPTZ NOT NULL DEFAULT now(),
                    device TEXT NOT NULL,
                    age_band TEXT,
                    size SMALLINT,
                    dht_mode TEXT,
                    wees REAL, poos REAL, changes REAL,
                    healthy_pct REAL, soiled_min REAL, overfull_min REAL,
                    humid_min REAL, avg_diaper_min REAL,
                    rash_level TEXT, hydration_level TEXT, gut_level TEXT,
                    sleep_min REAL, active_min REAL, wakeups REAL, wet_wakeups REAL,
                    now_state TEXT, sat REAL, p_full_1h REAL,
                    temp REAL, hum REAL,
                    events JSONB,
                    body JSONB NOT NULL
                )""")
            # Idempotent hypertable promotion (no-op if already a hypertable)
            cur.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM timescaledb_information.hypertables
                    WHERE hypertable_name = 'summaries')""")
            if not cur.fetchone()[0]:
                try:
                    cur.execute("SELECT create_hypertable('summaries', by_range('ts'))")
                except Exception:  # noqa: BLE001 — older Timescale API
                    cur.execute(
                        "SELECT create_hypertable('summaries', 'ts', if_not_exists => TRUE)")
            cur.execute("CREATE INDEX IF NOT EXISTS summaries_device_ts_idx ON summaries (device, ts DESC)")
            days = int(_env("RETENTION_DAYS", "365"))
            cur.execute(
                "SELECT add_retention_policy('summaries', %s::interval, if_not_exists => TRUE)",
                (f"{days} days",),
            )
            cur.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM timescaledb_information.continuous_aggregates
                    WHERE view_name = 'summaries_hourly')""")
            if not cur.fetchone()[0]:
                cur.execute("""
                    CREATE MATERIALIZED VIEW summaries_hourly
                    WITH (timescaledb.continuous) AS
                    SELECT time_bucket('1 hour', ts) AS bucket,
                           device,
                           avg(healthy_pct) AS avg_healthy_pct,
                           avg(soiled_min) AS avg_soiled_min,
                           avg(humid_min) AS avg_humid_min,
                           avg(wees) AS avg_wees,
                           count(*) AS n
                    FROM summaries
                    GROUP BY 1, 2
                    WITH NO DATA""")
                cur.execute("""
                    SELECT add_continuous_aggregate_policy(
                        'summaries_hourly',
                        start_offset => INTERVAL '3 days',
                        end_offset => INTERVAL '1 hour',
                        schedule_interval => INTERVAL '30 minutes',
                        if_not_exists => TRUE)""")
            # Refresh so seed/demo data is visible immediately
            try:
                cur.execute(
                    "CALL refresh_continuous_aggregate('summaries_hourly', NULL, NULL)")
            except Exception:  # noqa: BLE001
                pass

    def insert_summaries(self, items: list[dict]) -> int:
        if not items:
            return 0
        now = time.time()
        rows = [flatten_summary(s, now) for s in items]
        with self.lock:
            if self.backend == "tiger":
                self._insert_tiger(rows)
            else:
                self._insert_sqlite(rows)
        return len(rows)

    def _insert_sqlite(self, rows: list[dict]):
        self._sqlite.executemany(
            "INSERT INTO summaries (device, ts, received, body) VALUES (?,?,?,?)",
            [(r["device"], r["ts"], r["received"], json.dumps(r["body"])) for r in rows],
        )
        self._sqlite.commit()

    def _insert_tiger(self, rows: list[dict]):
        from psycopg.types.json import Jsonb
        sql = """
            INSERT INTO summaries (
                ts, received, device, age_band, size, dht_mode,
                wees, poos, changes, healthy_pct, soiled_min, overfull_min,
                humid_min, avg_diaper_min, rash_level, hydration_level, gut_level,
                sleep_min, active_min, wakeups, wet_wakeups,
                now_state, sat, p_full_1h, temp, hum, events, body
            ) VALUES (
                %(ts)s, %(received)s, %(device)s, %(age_band)s, %(size)s, %(dht_mode)s,
                %(wees)s, %(poos)s, %(changes)s, %(healthy_pct)s, %(soiled_min)s, %(overfull_min)s,
                %(humid_min)s, %(avg_diaper_min)s, %(rash_level)s, %(hydration_level)s, %(gut_level)s,
                %(sleep_min)s, %(active_min)s, %(wakeups)s, %(wet_wakeups)s,
                %(now_state)s, %(sat)s, %(p_full_1h)s, %(temp)s, %(hum)s, %(events)s, %(body)s
            )"""
        payloads = []
        for r in rows:
            payloads.append({
                **r,
                "ts": _ts_to_dt(r["ts"]),
                "received": _ts_to_dt(r["received"]),
                "events": Jsonb(r["events"]),
                "body": Jsonb(r["body"]),
            })
        with self._pg.cursor() as cur:
            cur.executemany(sql, payloads)

    def aggregate(self) -> dict:
        with self.lock:
            if self.backend == "tiger":
                return self._aggregate_tiger()
            return self._aggregate_sqlite()

    def _aggregate_sqlite(self) -> dict:
        since = time.time() - 24 * 3600
        rows = self._sqlite.execute("""SELECT s.body FROM summaries s JOIN (
                               SELECT device, MAX(id) AS id FROM summaries WHERE received > ? GROUP BY device
                             ) last ON s.id = last.id""", (since,)).fetchall()
        total = self._sqlite.execute("SELECT COUNT(*) FROM summaries").fetchone()[0]
        latest = [json.loads(r[0]) for r in rows]

        def avg(key, sub="today"):
            return _avg([x[sub].get(key) for x in latest if x.get(sub) and x[sub].get(key) is not None])

        rash = {}
        for x in latest:
            lv = (x.get("levels") or {}).get("rash")
            rash[lv] = rash.get(lv, 0) + 1
        # Lightweight hourly roll-up for local demos (Tiger uses continuous aggregates)
        since48 = time.time() - 48 * 3600
        raw = self._sqlite.execute(
            "SELECT ts, device, body FROM summaries WHERE ts > ? ORDER BY ts",
            (since48,),
        ).fetchall()
        buckets: dict[int, dict] = {}
        for ts, device, body in raw:
            b = int(ts // 3600) * 3600
            slot = buckets.setdefault(b, {"devices": set(), "healthy": [], "soiled": [], "humid": [], "wees": []})
            slot["devices"].add(device)
            try:
                s = json.loads(body)
                t = s.get("today") or {}
                if t.get("healthy_pct") is not None:
                    slot["healthy"].append(t["healthy_pct"])
                if t.get("soiled_min") is not None:
                    slot["soiled"].append(t["soiled_min"])
                if t.get("humid_min") is not None:
                    slot["humid"].append(t["humid_min"])
                if t.get("wees") is not None:
                    slot["wees"].append(t["wees"])
            except (TypeError, json.JSONDecodeError):
                pass
        hourly = []
        for b in sorted(buckets):
            slot = buckets[b]
            hourly.append({
                "bucket": datetime.fromtimestamp(b, tz=timezone.utc).isoformat(),
                "devices": len(slot["devices"]),
                "avg_healthy_pct": _avg(slot["healthy"]),
                "avg_soiled_min": _avg(slot["soiled"]),
                "avg_humid_min": _avg(slot["humid"]),
                "avg_wees": _avg(slot["wees"]),
            })
        return {
            "backend": "sqlite",
            "devices_24h": len(latest),
            "summaries_total": total,
            "avg_healthy_pct": avg("healthy_pct"),
            "avg_soiled_min": avg("soiled_min"),
            "avg_humid_min": avg("humid_min"),
            "avg_wees": avg("wees"),
            "rash_levels": {str(k) if k is not None else "null": v for k, v in rash.items()},
            "hourly": hourly,
        }

    def _aggregate_tiger(self) -> dict:
        with self._pg.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT ON (device)
                    device, healthy_pct, soiled_min, humid_min, wees, rash_level, body
                FROM summaries
                WHERE received > now() - INTERVAL '24 hours'
                ORDER BY device, ts DESC""")
            latest = cur.fetchall()
            cur.execute("SELECT COUNT(*) FROM summaries")
            total = cur.fetchone()[0]
            # Prefer continuous aggregate; fall back to raw if empty / not refreshed yet
            cur.execute("""
                SELECT bucket,
                       count(*)::int AS devices,
                       avg(avg_healthy_pct) AS avg_healthy_pct,
                       avg(avg_soiled_min) AS avg_soiled_min,
                       avg(avg_humid_min) AS avg_humid_min,
                       avg(avg_wees) AS avg_wees
                FROM summaries_hourly
                WHERE bucket > now() - INTERVAL '48 hours'
                GROUP BY bucket
                ORDER BY bucket""")
            hourly_rows = cur.fetchall()
            if not hourly_rows:
                cur.execute("""
                    SELECT time_bucket('1 hour', ts) AS bucket,
                           count(DISTINCT device)::int AS devices,
                           avg(healthy_pct), avg(soiled_min), avg(humid_min), avg(wees)
                    FROM summaries
                    WHERE ts > now() - INTERVAL '48 hours'
                    GROUP BY 1 ORDER BY 1""")
                hourly_rows = cur.fetchall()
                source = "hypertable"
            else:
                source = "continuous_aggregate"

        rash = {}
        for row in latest:
            lv = row[5]
            rash[lv] = rash.get(lv, 0) + 1

        def col_avg(idx):
            return _avg([r[idx] for r in latest])

        hourly = []
        for r in hourly_rows:
            bucket = r[0]
            if hasattr(bucket, "isoformat"):
                bucket = bucket.isoformat()
            hourly.append({
                "bucket": bucket,
                "devices": int(r[1] or 0),
                "avg_healthy_pct": round(float(r[2]), 4) if r[2] is not None else None,
                "avg_soiled_min": round(float(r[3]), 2) if r[3] is not None else None,
                "avg_humid_min": round(float(r[4]), 2) if r[4] is not None else None,
                "avg_wees": round(float(r[5]), 2) if r[5] is not None else None,
            })

        return {
            "backend": "tiger",
            "aggregate_source": source,
            "devices_24h": len(latest),
            "summaries_total": int(total),
            "avg_healthy_pct": col_avg(1),
            "avg_soiled_min": col_avg(2),
            "avg_humid_min": col_avg(3),
            "avg_wees": col_avg(4),
            "rash_levels": {str(k) if k is not None else "null": v for k, v in rash.items()},
            "hourly": hourly,
        }

    def run_retention(self, days: int):
        if self.backend != "sqlite":
            return
        with self.lock:
            self._sqlite.execute(
                "DELETE FROM summaries WHERE received < ?",
                (time.time() - days * 86400,),
            )
            self._sqlite.commit()

    def refresh_continuous_aggregate(self):
        if self.backend != "tiger":
            return
        with self.lock, self._pg.cursor() as cur:
            cur.execute("CALL refresh_continuous_aggregate('summaries_hourly', NULL, NULL)")

    def close(self):
        if self._sqlite is not None:
            self._sqlite.close()
        if self._pg is not None:
            self._pg.close()

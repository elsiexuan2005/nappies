"""EDGE · cloud-sync: the ONLY service that talks to the cloud.

Up (opt-in, SHARE=1): every SUMMARY_S seconds it turns the latest state + events into an
anonymised summary (no name, no raw stream, pseudonymous device hash, age band only),
spools it to a local SQLite queue and POSTs batches to the cloud ingest API.
If the cloud is unreachable, summaries wait in the spool and are sent later.

Down: checks the cloud model registry; downloads a newer model, verifies its SHA-256,
swaps it in atomically and tells inference to reload (podcare/models/updated).

env: DEVICE_ID  SHARE=1  CLOUD_URL=http://localhost:9001  API_KEY=dev-key
     REGISTRY_URL=http://localhost:9004  MODELS_PATH=edge_data/models.joblib
     DATA_DIR=edge_data  SUMMARY_S=60  MODEL_CHECK_S=300
"""
import hashlib
import json
import os
import secrets
import sqlite3
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from services.common import ROOT, env, forever, http_json, log, mqtt_client, topic  # noqa: E402

os.chdir(ROOT)
L = log("cloud-sync")
DEVICE = env("DEVICE_ID", "pod-001")
DATA = env("DATA_DIR", "edge_data")
os.makedirs(DATA, exist_ok=True)


def device_hash():
    """Pseudonymous ID: hash of device id + a per-install secret salt that never leaves the hub."""
    p = os.path.join(DATA, "salt")
    if not os.path.exists(p):
        with open(p, "w") as f:
            f.write(secrets.token_hex(16))
    with open(p) as f:
        return hashlib.sha256((DEVICE + f.read()).encode()).hexdigest()[:16]


def summarise(state, events, dh):
    t, n, cl = state.get("today", {}), state.get("now") or {}, state.get("climate", {})
    counts = {}
    for e in events:
        counts[e["type"]] = counts.get(e["type"], 0) + 1
    return {
        "device": dh, "ts": state.get("clock"), "age_band": state.get("baby", {}).get("age"),
        "size": state.get("baby", {}).get("size"), "dht_mode": cl.get("mode"),
        "events": counts,
        "today": {k: t.get(k) for k in ("wees", "poos", "changes", "healthy_pct", "soiled_min",
                                        "overfull_min", "humid_min", "avg_diaper_min")},
        "levels": {"rash": t.get("rash", {}).get("level"), "hydration": t.get("hydration", {}).get("level"),
                   "gut": t.get("gut", {}).get("level")},
        "sleep": {k: (state.get("sleep") or {}).get(k) for k in ("sleep_min", "active_min", "wakeups", "wet_wakeups")},
        "now": {"state": n.get("state"), "sat": n.get("sat"), "p_full_1h": n.get("p_full_1h")},
        "climate": {"temp": cl.get("temp"), "hum": cl.get("hum")},
    }


def main():
    share = env("SHARE", "1") == "1"
    cloud, key = env("CLOUD_URL", "http://localhost:9001"), env("API_KEY", "dev-key")
    registry = env("REGISTRY_URL", "http://localhost:9004")
    models_path = env("MODELS_PATH", os.path.join(DATA, "models.joblib"))
    dh = device_hash()
    db = sqlite3.connect(os.path.join(DATA, "spool.sqlite"), check_same_thread=False)
    db.execute("CREATE TABLE IF NOT EXISTS spool (id INTEGER PRIMARY KEY, body TEXT)")
    db.commit()
    lock = threading.Lock()
    latest = {"state": None, "events": []}

    def on_message(client, userdata, msg):
        if msg.topic.endswith("/state"):
            latest["state"] = json.loads(msg.payload)
        else:
            latest["events"].append(json.loads(msg.payload))

    c = mqtt_client("cloud-sync", on_message, [topic(DEVICE, "state"), topic(DEVICE, "events")])
    L.info("device %s -> pseudonym %s, sharing %s", DEVICE, dh, "ON" if share else "OFF")

    def uploader():
        while True:
            time.sleep(env("SUMMARY_S", "60", int))
            if not share or not latest["state"] or not latest["state"].get("ready"):
                continue
            s = summarise(latest["state"], latest["events"], dh)
            latest["events"] = []
            with lock:
                db.execute("INSERT INTO spool (body) VALUES (?)", (json.dumps(s),))
                db.commit()
                rows = db.execute("SELECT id, body FROM spool ORDER BY id LIMIT 200").fetchall()
            try:
                http_json("POST", f"{cloud}/v1/summaries", {"summaries": [json.loads(b) for _, b in rows]}, api_key=key)
                with lock:
                    db.execute(f"DELETE FROM spool WHERE id <= {rows[-1][0]}")
                    db.commit()
                L.info("uploaded %d summaries", len(rows))
            except Exception as e:  # noqa: BLE001
                L.warning("cloud unreachable (%s); %d summaries waiting in spool", e, len(rows))

    def model_puller():
        ver_file = os.path.join(DATA, "model_version")
        while True:
            try:
                man = http_json("GET", f"{registry}/v1/models/latest")
                have = open(ver_file).read().strip() if os.path.exists(ver_file) else None
                if man.get("version") and man["version"] != have:
                    blob = http_json("GET", f"{registry}/v1/models/{man['version']}/models.joblib", timeout=60)
                    if hashlib.sha256(blob).hexdigest() != man["sha256"]:
                        raise ValueError("checksum mismatch")
                    tmp = models_path + ".tmp"
                    with open(tmp, "wb") as f:
                        f.write(blob)
                    os.replace(tmp, models_path)
                    with open(ver_file, "w") as f:
                        f.write(man["version"])
                    c.publish("podcare/models/updated", man["version"], qos=1, retain=True)
                    L.info("installed model %s", man["version"])
            except Exception as e:  # noqa: BLE001
                L.warning("model check failed (%s)", e)
            time.sleep(env("MODEL_CHECK_S", "300", int))

    threading.Thread(target=uploader, daemon=True).start()
    threading.Thread(target=model_puller, daemon=True).start()
    forever()


if __name__ == "__main__":
    main()

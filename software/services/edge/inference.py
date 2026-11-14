"""EDGE · inference: runs the models on the live stream, detects events and computes
today's wellness metrics. Runs on the hub (phone/home hub/laptop) so alerts work
offline and raw baby data never has to leave the home.

  subscribes podcare/<device>/raw     rows from the gateway
             podcare/<device>/cmd     "change" resets the diaper session
             podcare/models/updated   reload models after cloud-sync downloads a new version
  publishes  podcare/<device>/state   full app state, every second (retained)
             podcare/<device>/events  wee / poo / change / sleep / wake (QoS 1)

env: DEVICE_ID  AGE  SIZE  BABY  DHT=nappy|room  DEMO_HISTORY=1  MODELS_PATH=models.joblib
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from services.common import ROOT, env, log, mqtt_client, topic  # noqa: E402

os.chdir(ROOT)
L = log("inference")
DEVICE = env("DEVICE_ID", "pod-001")


def models_path():
    p = env("MODELS_PATH", "models.joblib")
    return p if os.path.exists(p) else "models.joblib"


def main():
    from engine import Engine
    eng = Engine(mode="external", age=env("AGE", "3-6m"), size=env("SIZE", "3", int), baby=env("BABY", "Ava"),
                 seed_history=env("DEMO_HISTORY", "1") == "1", climate=env("DHT", "nappy"), models_path=models_path())
    published = len(eng.events)                     # don't re-publish simulated demo history

    def on_message(client, userdata, msg):
        try:
            if msg.topic.endswith("/raw"):
                m = json.loads(msg.payload)
                eng.feed(m.get("rows", []), m.get("meta"))
            elif msg.topic.endswith("/cmd"):
                if json.loads(msg.payload).get("action") == "change":
                    eng.action("change")
            elif msg.topic == "podcare/models/updated":
                eng.reload_models(models_path())
                L.info("reloaded models %s", msg.payload.decode())
        except Exception:  # noqa: BLE001
            L.exception("bad message on %s", msg.topic)

    c = mqtt_client("inference", on_message,
                    [topic(DEVICE, "raw"), topic(DEVICE, "cmd"), "podcare/models/updated"])
    L.info("device %s, models %s", DEVICE, models_path())
    while True:
        time.sleep(1)
        st = dict(eng.state)
        st["device"] = DEVICE
        c.publish(topic(DEVICE, "state"), json.dumps(st), qos=1, retain=True)
        if len(eng.events) > published:
            for e in eng.events[published:]:
                c.publish(topic(DEVICE, "events"), json.dumps({**e, "device": DEVICE}), qos=1)
            published = len(eng.events)


if __name__ == "__main__":
    main()

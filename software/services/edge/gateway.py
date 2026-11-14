"""EDGE · gateway: reads the pod (USB serial today, BLE in the product) or a simulated pod,
and publishes one batch of 1 Hz rows per second to MQTT.

  publishes  podcare/<device>/raw     {"rows": [...], "meta": {mode, speed, status, sim}}   QoS 0
             podcare/<device>/status  "online" / "offline" (retained, last will)
  subscribes podcare/<device>/cmd     demo controls for the simulated pod: wee, poo, asleep, speed, room_temp, change

Store-and-forward: if the broker is unreachable, rows are buffered (up to 1 h) and sent on reconnect.

env: DEVICE_ID=pod-001  MODE=sim|serial  PORTS=auto|COM3,COM4  AGE=3-6m  SIZE=3  DHT=nappy|room  SPEED=60
     MQTT_HOST, MQTT_PORT
"""
import json
import os
import sys
import time
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from services.common import ROOT, env, forever, log, mqtt_client, topic  # noqa: E402

os.chdir(ROOT)
L = log("gateway")
DEVICE = env("DEVICE_ID", "pod-001")
MODE = env("MODE", "sim")


def main():
    from sensor import load_cal
    from simulate import SimPod

    state = {"speed": env("SPEED", "60", int) if MODE == "sim" else 1}
    pod, ps = None, None
    if MODE == "sim":
        pod = SimPod(load_cal(), age=env("AGE", "3-6m"), size=env("SIZE", "3", int),
                     climate=env("DHT", "nappy"), worn=True)
    else:
        from pod_stream import PodStream
        ps = PodStream([env("PORTS", "auto")])        # "auto" = every Arduino board (USB VID 0x2341)
    t0 = time.time()

    def on_message(client, userdata, msg):
        try:
            cmd = json.loads(msg.payload)
        except ValueError:
            return
        a, v = cmd.get("action"), cmd.get("value")
        if not pod:
            return
        if a == "wee":
            pod.wee()
        elif a == "poo":
            pod.poo()
        elif a == "asleep":
            pod.asleep = bool(v)
        elif a == "speed":
            state["speed"] = max(1, min(600, int(v)))
        elif a == "room_temp":
            pod.room_temp = float(v)
        elif a == "change":
            pod.change()
        L.info("command %s %s", a, "" if v is None else v)

    c = mqtt_client("gateway", on_message, [topic(DEVICE, "cmd")], will=(topic(DEVICE, "status"), "offline"))
    c.publish(topic(DEVICE, "status"), "online", qos=1, retain=True)
    backlog = deque(maxlen=3600)
    L.info("device %s, mode %s", DEVICE, MODE)

    while True:
        time.sleep(1)
        if pod:
            rows = []
            for _ in range(state["speed"]):
                r = pod.step()
                r["t"] = t0 + r["t"]
                rows.append(r)
            status = {"sensors": True, "imu": True, "climate": True}
            sim = {"asleep": pod.asleep, "room_temp": pod.room_temp}
        else:
            rows, status, sim = ps.read(), ps.status(), None
        backlog.extend(rows)
        if not backlog:
            continue
        if not c.is_connected():
            continue                                  # keep buffering until the broker is back
        batch = list(backlog)
        msg = {"rows": batch, "meta": {"mode": MODE, "speed": state["speed"], "status": status, "sim": sim}}
        if c.publish(topic(DEVICE, "raw"), json.dumps(msg), qos=0).rc == 0:
            backlog.clear()


if __name__ == "__main__":
    main()
    forever()

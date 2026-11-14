"""Run the whole microservice stack on one laptop, without Docker.

  python run_local.py                                   # simulated pod, edge + cloud
  python run_local.py --mode serial --port COM3 --port COM4
  python run_local.py --edge-only                        # hub only (no cloud)
  python run_local.py --cloud-only                       # cloud services only

Starts: MQTT broker (pure-Python dev broker), the edge services (gateway, inference,
parent-api, cloud-sync) and the cloud services (ingest, fleet, lab, model-registry),
after running the trainer once if no model has been published yet.

  Parent app (edge)      http://localhost:8000/
  Business (cloud)       http://localhost:9002/business
  Product Lab (cloud)    http://localhost:9003/lab
  Ingest API (cloud)     http://localhost:9001/v1/aggregate
  Model registry (cloud) http://localhost:9004/v1/models/latest
Ctrl+C stops everything.
"""
import argparse
import os
import signal
import socket
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
COLORS = ["36", "33", "35", "32", "34", "31", "96", "93", "95"]


def port_open(port):
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def start(name, script, env_extra, procs, color):
    env = {**os.environ, "PYTHONUNBUFFERED": "1", **{k: str(v) for k, v in env_extra.items()}}
    p = subprocess.Popen([PY, script], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def pump():
        for line in p.stdout:
            sys.stdout.write(f"\033[{color}m{name:>14}\033[0m | {line}")
    threading.Thread(target=pump, daemon=True).start()
    procs.append((name, p))
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["sim", "serial"], default="sim")
    ap.add_argument("--port", action="append", default=[])
    ap.add_argument("--dht", choices=["nappy", "room"], default="nappy")
    ap.add_argument("--edge-only", action="store_true")
    ap.add_argument("--cloud-only", action="store_true")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    ap.add_argument("--sessions", type=int, default=150, help="trainer sessions (first run only)")
    ap.add_argument("--summary-s", type=int, default=20, help="how often cloud-sync uploads (s)")
    a = ap.parse_args()

    common = {"MQTT_HOST": "127.0.0.1", "MQTT_PORT": a.mqtt_port, "DEVICE_ID": "pod-001", "API_KEY": "dev-key",
              "CLOUD_DATA": "cloud_data", "BENCH_DIR": "cloud_data/bench", "REGISTRY_DIR": "cloud_data/registry"}
    procs, ci = [], iter(COLORS * 3)
    edge = not a.cloud_only
    cloud = not a.edge_only

    if cloud:
        if not os.path.exists(os.path.join(ROOT, "cloud_data/registry/latest.json")):
            print("No published model yet: running the trainer once ...")
            subprocess.run([PY, "services/cloud/trainer.py"], cwd=ROOT, check=True,
                           env={**os.environ, **{k: str(v) for k, v in common.items()}, "SESSIONS": str(a.sessions)})
        start("model-registry", "services/cloud/model_registry.py", {**common, "PORT": 9004}, procs, next(ci))
        start("ingest", "services/cloud/ingest.py", {**common, "PORT": 9001}, procs, next(ci))
        start("fleet", "services/cloud/fleet_service.py", {**common, "PORT": 9002, "INGEST_URL": "http://127.0.0.1:9001"}, procs, next(ci))
        start("lab", "services/cloud/lab_service.py", {**common, "PORT": 9003}, procs, next(ci))

    if edge:
        if not port_open(a.mqtt_port):
            start("mqtt-broker", "services/edge/dev_broker.py", {**common}, procs, next(ci))
            for _ in range(50):
                if port_open(a.mqtt_port):
                    break
                time.sleep(0.1)
        gw = {**common, "MODE": a.mode, "PORTS": ",".join(a.port) or "auto", "DHT": a.dht}
        start("gateway", "services/edge/gateway.py", gw, procs, next(ci))
        start("inference", "services/edge/inference.py",
              {**common, "DHT": a.dht, "MODELS_PATH": "edge_data/models.joblib"}, procs, next(ci))
        start("parent-api", "services/edge/parent_api.py", {**common, "PORT": 8000}, procs, next(ci))
        if cloud:
            start("cloud-sync", "services/edge/cloud_sync.py",
                  {**common, "CLOUD_URL": "http://127.0.0.1:9001", "REGISTRY_URL": "http://127.0.0.1:9004",
                   "MODELS_PATH": "edge_data/models.joblib", "SUMMARY_S": a.summary_s, "MODEL_CHECK_S": 60}, procs, next(ci))

    time.sleep(2)
    print("\n  Parent app (edge)      http://localhost:8000/" if edge else "")
    if cloud:
        print("  Business (cloud)       http://localhost:9002/business\n"
              "  Product Lab (cloud)    http://localhost:9003/lab\n"
              "  Model registry         http://localhost:9004/v1/models/latest\n")
    try:
        while True:
            time.sleep(1)
            for name, p in procs:
                if p.poll() is not None:
                    print(f"!! {name} exited with code {p.returncode}")
                    procs.remove((name, p))
    except KeyboardInterrupt:
        pass
    finally:
        for _, p in procs:
            p.send_signal(signal.SIGTERM)
        for _, p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    main()

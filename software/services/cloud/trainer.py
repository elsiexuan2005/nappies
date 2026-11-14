"""CLOUD · trainer (batch job): trains the models on synthetic data plus every real
bench run uploaded so far, then publishes a new version to the model registry.
Run once (Compose) or on a schedule (Kubernetes CronJob / Cloud Run Job).

env: SESSIONS=250  BENCH_DIR=cloud_data/bench  REGISTRY_DIR=cloud_data/registry  SCHEDULE_S=0 (0 = run once)
"""
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from services.common import ROOT, env, log  # noqa: E402

os.chdir(ROOT)
L = log("trainer")
REG = env("REGISTRY_DIR", "cloud_data/registry")
BENCH = env("BENCH_DIR", "cloud_data/bench")


def run_once():
    version = time.strftime("%Y%m%d-%H%M%S")
    out_dir = os.path.join(REG, version)
    os.makedirs(out_dir, exist_ok=True)
    cmd = [sys.executable, "train.py", "--sessions", env("SESSIONS", "250"), "--out", os.path.join(out_dir, "models.joblib")]
    if glob.glob(os.path.join(BENCH, "*.csv")):
        cmd += ["--bench", os.path.join(BENCH, "*.csv")]
    L.info("training version %s: %s", version, " ".join(cmd[1:]))
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    shutil.copy("models_report.txt", os.path.join(out_dir, "report.txt"))
    with open(os.path.join(out_dir, "models.joblib"), "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    report = open(os.path.join(out_dir, "report.txt")).read()
    acc = next((ln.split()[1] for ln in report.splitlines() if ln.strip().startswith("accuracy")), None)
    manifest = {"version": version, "sha256": sha, "created": time.time(), "state_accuracy_synthetic": acc,
                "bench_runs_used": len(glob.glob(os.path.join(BENCH, "*.csv")))}
    tmp = os.path.join(REG, "latest.json.tmp")
    with open(tmp, "w") as f:
        json.dump(manifest, f)
    os.replace(tmp, os.path.join(REG, "latest.json"))        # atomic publish
    L.info("published %s (sha256 %s…)", version, sha[:12])


if __name__ == "__main__":
    every = env("SCHEDULE_S", "0", int)
    while True:
        run_once()
        if not every:
            break
        time.sleep(every)

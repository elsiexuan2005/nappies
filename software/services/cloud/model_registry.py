"""CLOUD · model-registry: versioned models that edge hubs download (over-the-air updates).

  GET /v1/models/latest                    {"version", "sha256", "created", "metrics"}
  GET /v1/models/<version>/models.joblib   the model file

Layout on disk (shared volume here; object storage in production):
  REGISTRY_DIR/latest.json
  REGISTRY_DIR/<version>/models.joblib, report.txt

env: PORT=9004  REGISTRY_DIR=cloud_data/registry
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from services.common import ROOT, env, forever, log, serve  # noqa: E402

os.chdir(ROOT)
L = log("model-registry")
REG = env("REGISTRY_DIR", "cloud_data/registry")
os.makedirs(REG, exist_ok=True)


def latest(h, _):
    p = os.path.join(REG, "latest.json")
    if not os.path.exists(p):
        return 404, {"error": "no model published yet - run the trainer"}
    with open(p) as f:
        return 200, json.load(f)


def model_file(h, _):
    m = re.fullmatch(r"/v1/models/([0-9A-Za-z_-]+)/models\.joblib", h.path.split("?")[0])
    if not m:
        return 404, {"error": "not found"}
    p = os.path.join(REG, m.group(1), "models.joblib")
    if not os.path.exists(p):
        return 404, {"error": "unknown version"}
    with open(p, "rb") as f:
        return 200, f.read(), "application/octet-stream"


if __name__ == "__main__":
    serve(env("PORT", "9004", int), {"/v1/models/latest": latest, "/v1/models/*": model_file},
          service="model-registry")
    forever()

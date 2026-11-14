"""CLOUD · lab: Product Lab for P&G R&D. Analyses bench runs uploaded through ingest
(shared storage) with the latest model from the registry.

  GET /lab        Product Lab page
  GET /api/lab    comparison metrics, findings and validation

env: PORT=9003  BENCH_DIR=cloud_data/bench  REGISTRY_DIR=cloud_data/registry
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from services.common import ROOT, env, forever, log, serve  # noqa: E402

os.chdir(ROOT)
L = log("lab")
BENCH = env("BENCH_DIR", "cloud_data/bench")
REG = env("REGISTRY_DIR", "cloud_data/registry")
cache = {"key": None, "data": None}


def model_path():
    try:
        with open(os.path.join(REG, "latest.json")) as f:
            return os.path.join(REG, json.load(f)["version"], "models.joblib")
    except (OSError, ValueError, KeyError):
        return "models.joblib"


def main():
    import benchlib

    def get_lab(h, _):
        pats = (os.path.join(BENCH, "*.csv"), "bench/examples/*.csv")
        files = sorted(sum((glob.glob(p) for p in pats), []))
        mp = model_path()
        key = (mp, tuple((f, os.path.getmtime(f)) for f in files))
        if key != cache["key"]:
            cache["data"] = benchlib.analyse(pats, models_path=mp)
            cache["data"]["model"] = os.path.basename(os.path.dirname(mp)) or "local"
            cache["key"] = key
        return 200, cache["data"]

    serve(env("PORT", "9003", int), {"/api/lab": get_lab}, None, {"/lab": "lab.html", "/": "lab.html"}, service="lab")
    forever()


if __name__ == "__main__":
    main()

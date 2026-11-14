"""Upload recorded bench runs to the cloud ingest service (R&D lab → cloud Product Lab).

  python upload_bench.py bench/*.csv --cloud http://localhost:9001 --key dev-key
"""
import argparse
import os

from services.common import http_json


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--cloud", default=os.environ.get("CLOUD_URL", "http://localhost:9001"))
    ap.add_argument("--key", default=os.environ.get("API_KEY", "dev-key"))
    a = ap.parse_args()
    for p in a.files:
        with open(p, "rb") as f:
            r = http_json("POST", f"{a.cloud}/v1/bench-runs?name={os.path.basename(p)}", raw=f.read(),
                          api_key=a.key, ctype="text/csv")
        print(p, "->", r)


if __name__ == "__main__":
    main()

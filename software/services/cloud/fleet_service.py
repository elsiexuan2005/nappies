"""CLOUD · fleet: the P&G business dashboard and its API.
Combines the simulated fleet projection with live roll-ups from connected hubs (via ingest).
The "live" block includes Tiger Data hourly continuous aggregates when ingest has DATABASE_URL set.

  GET /business        dashboard page
  GET /api/business    fleet insights + "live" block from ingest (/v1/aggregate)

env: PORT=9002  INGEST_URL=http://localhost:9001  FLEET_N=3000
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from services.common import ROOT, env, forever, http_json, log, serve  # noqa: E402

os.chdir(ROOT)
L = log("fleet")


def main():
    import fleet
    L.info("simulating fleet ...")
    data = fleet.build(n=env("FLEET_N", "3000", int))
    ingest = env("INGEST_URL", "http://localhost:9001")

    def get_business(h, _):
        out = dict(data)
        try:
            out["live"] = http_json("GET", f"{ingest}/v1/aggregate", timeout=3)
        except Exception as e:  # noqa: BLE001
            out["live"] = {"error": str(e)}
        return 200, out

    serve(env("PORT", "9002", int), {"/api/business": get_business}, None,
          {"/business": "business.html", "/": "business.html"}, service="fleet")
    forever()


if __name__ == "__main__":
    main()

"""EDGE · parent-api: serves the parent app and its API on the home network.
In the product this is the phone app; here it's a local web app.

  GET  /              parent app
  GET  /api/state     latest state from podcare/<device>/state
  POST /api/action    publishes a command to podcare/<device>/cmd

env: DEVICE_ID  PORT=8000
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from services.common import env, forever, log, mqtt_client, serve, topic  # noqa: E402

L = log("parent-api")
DEVICE = env("DEVICE_ID", "pod-001")
latest = {"state": None}


def main():
    def on_message(client, userdata, msg):
        latest["state"] = msg.payload

    c = mqtt_client("parent-api", on_message, [topic(DEVICE, "state")])

    def get_state(h, _):
        if latest["state"] is None:
            return 503, {"error": "waiting for the inference service"}
        return 200, latest["state"], "application/json"

    def post_action(h, body):
        cmd = json.loads(body or b"{}")
        c.publish(topic(DEVICE, "cmd"), json.dumps(cmd), qos=1)
        return 200, {"ok": True}

    serve(env("PORT", "8000", int), {"/api/state": get_state}, {"/api/action": post_action},
          {"/": "parent.html"}, service="parent-api")
    forever()


if __name__ == "__main__":
    main()

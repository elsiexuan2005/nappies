"""Web server for the two dashboards.

  python server.py                                   # simulator (no hardware)
  python server.py --mode serial                     # auto-detects the UNO Q + Nicla
  python server.py --mode serial --port COM3 --port COM4
  options: --age 0-3m|3-6m|6-12m  --size 1-5  --baby Ava  --no-demo-history  --http-port 8000
           --dht nappy|room   (DHT11 inside the nappy = skin climate [default], or near the cot = room)

Then open:
  http://localhost:8000/           parent app (phone UI + demo controls)
  http://localhost:8000/business   P&G business dashboard
  http://localhost:8000/lab        Product Lab: bench comparison + model validation
"""
import argparse
import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import glob

import benchlib
import fleet
from engine import Engine

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
ROUTES = {"/": "parent.html", "/business": "business.html", "/lab": "lab.html"}
_lab_cache = {"key": None, "data": None}


def lab_data():
    """Re-analyse bench runs only when files change (new runs appear while the server is up)."""
    files = sorted(glob.glob("bench/*.csv") + glob.glob("bench/examples/*.csv"))
    key = tuple((f, os.path.getmtime(f)) for f in files)
    if key != _lab_cache["key"]:
        _lab_cache["data"] = benchlib.analyse()
        _lab_cache["key"] = key
    return _lab_cache["data"]


def make_handler(engine, business):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")
            if path == "/api/state":
                return self._send(200, json.dumps(engine.state).encode())
            if path == "/api/business":
                return self._send(200, json.dumps(business).encode())
            if path == "/api/lab":
                return self._send(200, json.dumps(lab_data()).encode())
            name = ROUTES.get(path, path.lstrip("/"))
            full = os.path.normpath(os.path.join(WEB, name))
            if not full.startswith(WEB) or not os.path.isfile(full):
                return self._send(404, b"not found", "text/plain")
            with open(full, "rb") as f:
                self._send(200, f.read(), mimetypes.guess_type(full)[0] or "application/octet-stream")

        def do_POST(self):
            if self.path != "/api/action":
                return self._send(404, b"{}")
            n = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                return self._send(400, b'{"ok":false}')
            ok = engine.action(body.get("action"), body.get("value"))
            self._send(200, json.dumps({"ok": ok}).encode())

    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["sim", "serial"], default="sim")
    ap.add_argument("--port", action="append", default=[])
    ap.add_argument("--age", default="3-6m")
    ap.add_argument("--size", type=int, default=3)
    ap.add_argument("--baby", default="Ava")
    ap.add_argument("--no-demo-history", action="store_true")
    ap.add_argument("--http-port", type=int, default=8000)
    ap.add_argument("--dht", choices=["nappy", "room"], default="nappy",
                    help="where the DHT11 is: inside the nappy (skin climate) or near the cot (room)")
    a = ap.parse_args()

    engine = Engine(mode=a.mode, ports=a.port, age=a.age, size=a.size, baby=a.baby,
                    seed_history=not a.no_demo_history, climate=a.dht)
    print("Simulating fleet for the business dashboard ...")
    business = fleet.build()
    srv = ThreadingHTTPServer(("0.0.0.0", a.http_port), make_handler(engine, business))
    print(f"Parent app:          http://localhost:{a.http_port}/")
    print(f"Business dashboard:  http://localhost:{a.http_port}/business")
    print(f"Product Lab:         http://localhost:{a.http_port}/lab")
    srv.serve_forever()


if __name__ == "__main__":
    main()

"""Shared helpers for all PodCare microservices.

Each service is a small Python process configured by environment variables.
The repo root (config, sensor, features, engine, benchlib, fleet, ...) is the
shared core library; services import it directly.
"""
import json
import logging
import mimetypes
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
WEB = os.path.join(ROOT, "web")

TOPIC_ROOT = "podcare"


def topic(device, kind):
    """podcare/<device>/<kind>  kind = raw | state | events | cmd | status"""
    return f"{TOPIC_ROOT}/{device}/{kind}"


def env(name, default=None, cast=str):
    v = os.environ.get(name, default)
    return cast(v) if v is not None else None


def log(service):
    logging.basicConfig(level=logging.INFO, format=f"%(asctime)s [{service}] %(message)s", datefmt="%H:%M:%S")
    return logging.getLogger(service)


# ----------------------------------------------------------------------------- MQTT
def mqtt_client(service, on_message=None, subscriptions=(), will=None):
    """Connected paho client with auto-reconnect. Subscriptions are re-made on every reconnect."""
    import paho.mqtt.client as mqtt
    host, port = env("MQTT_HOST", "localhost"), env("MQTT_PORT", "1883", int)
    lg = logging.getLogger(service)
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{service}-{os.getpid()}")
    if env("MQTT_USER"):
        c.username_pw_set(env("MQTT_USER"), env("MQTT_PASSWORD", ""))
    if will:
        c.will_set(*will, qos=1, retain=True)

    def on_connect(client, userdata, flags, rc, props=None):
        lg.info("MQTT connected to %s:%s", host, port)
        for s in subscriptions:
            client.subscribe(s, qos=1)

    c.on_connect = on_connect
    if on_message:
        c.on_message = on_message
    c.reconnect_delay_set(min_delay=1, max_delay=10)
    while True:
        try:
            c.connect(host, port, keepalive=30)
            break
        except OSError as e:
            lg.warning("MQTT broker not reachable (%s), retrying in 2 s", e)
            time.sleep(2)
    c.loop_start()
    return c


# ----------------------------------------------------------------------------- HTTP
def serve(port, get_routes=None, post_routes=None, static_pages=None, service="http", api_key=None):
    """Tiny JSON/static HTTP server.
    get_routes / post_routes: {path: fn(handler, body) -> (code, obj_or_bytes, content_type)}
    static_pages: {path: filename in web/}; /vendor/* is always served from web/vendor.
    api_key: if set, POST requests must send header X-Api-Key."""
    get_routes, post_routes, static_pages = get_routes or {}, post_routes or {}, static_pages or {}
    lg = logging.getLogger(service)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body, ctype="application/json"):
            if not isinstance(body, (bytes, bytearray)):
                body = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/healthz":
                return self.send(200, {"ok": True, "service": service})
            if path == "/favicon.ico":
                return self.send(204, b"", "image/x-icon")
            for p, fn in get_routes.items():
                if path == p or (p.endswith("*") and path.startswith(p[:-1])):
                    return self.send(*fn(self, None))
            name = static_pages.get(path) or (path.lstrip("/") if path.startswith("/vendor/") else None)
            if name:
                full = os.path.normpath(os.path.join(WEB, name))
                if full.startswith(WEB) and os.path.isfile(full):
                    with open(full, "rb") as f:
                        return self.send(200, f.read(), mimetypes.guess_type(full)[0] or "application/octet-stream")
            self.send(404, {"error": "not found"})

        def do_POST(self):
            path = self.path.split("?")[0]
            fn = post_routes.get(path)
            if not fn:
                return self.send(404, {"error": "not found"})
            if api_key and self.headers.get("X-Api-Key") != api_key:
                return self.send(401, {"error": "bad api key"})
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n) if n else b""
            try:
                self.send(*fn(self, body))
            except Exception as e:  # noqa: BLE001
                lg.exception("POST %s failed", path)
                self.send(500, {"error": str(e)})

    srv = ThreadingHTTPServer(("0.0.0.0", port), H)
    lg.info("HTTP on :%s", port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def http_json(method, url, obj=None, api_key=None, timeout=10, raw=None, ctype="application/json"):
    import urllib.request
    data = raw if raw is not None else (json.dumps(obj).encode() if obj is not None else None)
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", ctype)
    if api_key:
        req.add_header("X-Api-Key", api_key)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        return json.loads(body) if r.headers.get("Content-Type", "").startswith("application/json") else body


def forever():
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass

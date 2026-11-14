"""Reads the sensor board (Arduino UNO Q: moisture, gas, DHT11), optional outer-leak board,
and the Nicla (IMU) at the same time and merges them into ONE row per second:
t, moisture, gas, acc_*, gyro_mean, temp, hum, leak_detected, leak_raw.

  python pod_stream.py --list                  # show USB serial ports (Arduino boards marked)
  python pod_stream.py                         # auto-detect every Arduino board and show live data
  python pod_stream.py --port COM3 --port COM4 # or name the ports

Lines it understands (port order doesn't matter - each line says what it carries):
  JSON (team format, hardware/*.ino):
    {"timestamp_ms":..,"sensor_raw":..}                      moisture_sensor.ino (moisture only)
    {"timestamp_ms":..,"sensor_raw":..,"leak_detected":..}   leak_detection.ino (outer leak + LED)
    {"timestamp_ms":..,"gas_raw":..}                         gas_sensor.ino (gas only)
    {"timestamp_ms":..,"sensor_raw":..,"gas_raw":..,"temp_c":..,"humidity":..}   pod_sensors.ino
    {"timestamp_ms":..,"acc_mean":..,"acc_std":..,"gyro_mean":..}               nicla_imu.ino
    {"timestamp_ms":..,"motion_pixels":..,"moving":..,[acc_*, gyro_mean]}       nicla_vision/motion_stream.py
  Text from nicla_vision/motion_detection.py (OpenMV camera):  "motion_pixels: 340 movement_count: 2"
  CSV (older sketches):  S,ms,moisture,gas[,temp,hum]   I,ms,acc_mean,acc_std,gyro_mean

Presence of leak_detected means the line is from leak_detection.ino: sensor_raw is the outer
probe and must not overwrite inside-nappy moisture.

Motion: if the Nicla sends IMU values they are used directly. If it only sends camera motion
(changed pixels per frame), the busiest frame of each second is converted to the same activity
scale (CAMERA_* below), so the still/moving and sleep logic works either way; calibrate.py's
"still"/"active" labels then fine-tune the thresholds.
"""
import argparse
import json
import re
import threading
import time

import serial

ARDUINO_VID = 0x2341       # Arduino USB vendor ID (UNO Q, Nicla Vision, ...)
CAMERA_PX_SCALE = 40.0     # camera motion -> activity: 200 changed pixels (motion_detection.py's
CAMERA_ACC_STILL = 0.004   #   MIN_PIXELS) lands right on the still/moving boundary
CAMERA_GYRO_STILL = 0.6
_MOTION_TXT = re.compile(r"motion_pixels:\s*(\d+)")

COLS = ["moisture", "gas", "acc_mean", "acc_std", "gyro_mean", "temp", "hum"]
STALE_S = 3.0


class PodStream:
    def __init__(self, ports, baud=115200, period=1.0):
        self.latest = {c: None for c in COLS}
        self.latest["leak_detected"] = False
        self.latest["leak_raw"] = None
        self.seen = {"S": 0.0, "I": 0.0, "D": 0.0, "M": 0.0, "L": 0.0}  # L = outer leak board
        self._motion = []                                       # (time, changed pixels) from the camera
        self.rows, self.lock = [], threading.Lock()
        self.errors = []
        self.serials = []
        ports = resolve_ports(ports)
        for p in ports:
            s = serial.Serial(p, baud, timeout=1)
            self.serials.append(s)
            threading.Thread(target=self._read, args=(s,), daemon=True).start()
        self.period = period
        threading.Thread(target=self._tick, daemon=True).start()

    def _read(self, ser):
        while True:
            try:
                line = ser.readline().decode(errors="ignore").strip()
            except Exception as e:  # noqa: BLE001  (unplugged etc.)
                self.errors.append(f"{ser.port}: {e}")
                return
            if not line or line.startswith("#"):
                continue
            if line.startswith("{"):
                self._json(line)
                continue
            mt = _MOTION_TXT.search(line)
            if mt:                                   # OpenMV motion_detection.py output
                self._add_motion(float(mt.group(1)))
                continue
            p = line.split(",")
            try:
                with self.lock:
                    if p[0] == "S" and len(p) in (4, 6):
                        self.latest["moisture"], self.latest["gas"] = float(p[2]), float(p[3])
                        self.seen["S"] = time.time()
                        if len(p) == 6 and p[4] != "nan" and p[5] != "nan":
                            self.latest["temp"], self.latest["hum"] = float(p[4]), float(p[5])
                            self.seen["D"] = time.time()
                    elif p[0] == "I" and len(p) == 5:
                        self.latest["acc_mean"], self.latest["acc_std"], self.latest["gyro_mean"] = map(float, p[2:5])
                        self.seen["I"] = time.time()
                    elif len(p) == 6:
                        v = list(map(float, p))
                        self.latest.update(dict(zip(COLS[:5], v[1:])))
                        self.seen["S"] = self.seen["I"] = time.time()
            except ValueError:
                continue

    def _add_motion(self, px):
        now = time.time()
        with self.lock:
            self._motion.append((now, px))
            self._motion = [m for m in self._motion if now - m[0] < 3]
            self.seen["M"] = now

    def _camera_activity(self, now):
        recent = [px for t, px in self._motion if now - t < 1.2]
        if not recent:
            return None
        px = max(recent)
        k = 1 + px / CAMERA_PX_SCALE
        return {"acc_mean": 1.0, "acc_std": CAMERA_ACC_STILL * k, "gyro_mean": CAMERA_GYRO_STILL * k}

    def _json(self, line):
        try:
            d = json.loads(line)
        except ValueError:
            return
        now = time.time()

        def num(k):
            v = d.get(k)
            try:
                v = float(v)
            except (TypeError, ValueError):
                return None
            return v if v == v else None          # drop NaN

        with self.lock:
            # Outer-leak board: same sensor_raw key as moisture, but leak_detected marks it.
            if "leak_detected" in d:
                raw = num("sensor_raw") if "sensor_raw" in d else num("moisture_raw")
                if raw is not None:
                    self.latest["leak_raw"] = raw
                flag = d.get("leak_detected")
                self.latest["leak_detected"] = flag is True or flag == "true" or flag == 1
                self.seen["L"] = now
            else:
                m = num("sensor_raw") if "sensor_raw" in d else num("moisture_raw")
                if m is not None:
                    self.latest["moisture"] = m
                    self.seen["S"] = now
            g = num("gas_raw")
            if g is not None:
                self.latest["gas"] = g
                self.seen["S"] = now
            t_, h_ = num("temp_c"), num("humidity")
            if t_ is not None and h_ is not None:
                self.latest["temp"], self.latest["hum"] = t_, h_
                self.seen["D"] = now
            if num("motion_pixels") is not None:
                self._motion.append((now, num("motion_pixels")))
                self._motion = [x for x in self._motion if now - x[0] < 3]
                self.seen["M"] = now
            if num("acc_std") is not None:
                self.latest["acc_mean"] = num("acc_mean") or 1.0
                self.latest["acc_std"] = num("acc_std")
                self.latest["gyro_mean"] = num("gyro_mean") or 0.0
                self.seen["I"] = now

    def _tick(self):
        nxt = time.time()
        while True:
            nxt += self.period
            time.sleep(max(0, nxt - time.time()))
            with self.lock:
                if self.latest["moisture"] is None:      # need the sensor board at least
                    continue
                now = time.time()
                row = {"t": now, **self.latest}
                if now - self.seen["L"] >= STALE_S:      # no recent outer-leak board
                    row["leak_detected"] = False
                    row["leak_raw"] = None
                if now - self.seen["I"] >= STALE_S:      # no IMU: use camera motion if we have it
                    cam = self._camera_activity(now)
                    if cam:
                        row.update(cam)
                    elif row["acc_std"] is None:         # no motion source at all yet
                        row.update(acc_mean=1.0, acc_std=0.0, gyro_mean=0.0)
                if row["gas"] is None:                   # moisture-only sketch: flat gas = "no smell"
                    row["gas"] = 0.0
                self.rows.append(row)

    def read(self, speed=1):
        with self.lock:
            out, self.rows = self.rows, []
        return out

    def set_alert_led(self, on):
        """Tell every open board to turn the alert LED on/off (parent app red state).

        Boards that understand it: leak_detection.ino, pod_sensors.ino (USE_LED).
        Harmless no-op on sketches that ignore inbound JSON.
        """
        msg = b'{"led":true}\n' if on else b'{"led":false}\n'
        with self.lock:
            for s in self.serials:
                try:
                    s.write(msg)
                except Exception as e:  # noqa: BLE001
                    self.errors.append(f"{getattr(s, 'port', '?')}: led write: {e}")

    def status(self):
        now = time.time()
        imu, cam = now - self.seen["I"] < STALE_S, now - self.seen["M"] < STALE_S
        return {"sensors": now - self.seen["S"] < STALE_S, "imu": imu or cam,
                "motion_source": "imu" if imu else ("camera" if cam else None),
                "climate": now - self.seen["D"] < 10,
                "leak": now - self.seen["L"] < STALE_S}


def _is_board(p):
    text = f"{p.description} {p.manufacturer or ''} {p.product or ''}".lower()
    return p.vid == ARDUINO_VID or "openmv" in text or "nicla" in text


def arduino_ports():
    """Every connected Arduino board (same USB vendor-ID check as hardware/record_sensor.py),
    plus the Nicla when it runs OpenMV firmware (matched by name)."""
    from serial.tools import list_ports as lp
    return [p.device for p in lp.comports() if _is_board(p)]


def resolve_ports(ports):
    """[] or ["auto"] -> all Arduino boards; otherwise the ports as given (comma-separated allowed)."""
    flat = [x.strip() for p in (ports or []) for x in str(p).split(",") if x.strip()]
    if not flat or flat == ["auto"]:
        found = arduino_ports()
        if not found:
            raise SystemExit("No Arduino boards found. Check the USB cables, or pass --port explicitly "
                             "(python pod_stream.py --list).")
        print("Auto-detected Arduino boards:", ", ".join(found))
        return found
    return flat


def list_ports():
    from serial.tools import list_ports as lp
    for p in lp.comports():
        tag = "  <- Arduino / Nicla" if _is_board(p) else ""
        print(f"{p.device:<25} {p.description}{tag}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", action="append", default=[])
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list:
        list_ports()
        raise SystemExit
    ps = PodStream(resolve_ports(a.port))
    print("t        moisture    gas   acc_std  gyro   temp   hum  leak  [sensors / nicla / leak]")
    while True:
        time.sleep(1)
        st = ps.status()
        for r in ps.read():
            leak = "YES" if r.get("leak_detected") else ("--" if r.get("leak_raw") is None else "no")
            print(f"{time.strftime('%H:%M:%S', time.localtime(r['t']))} {r['moisture']:8.1f} {r['gas']:7.1f} "
                  f"{r['acc_std']:8.4f} {r['gyro_mean']:6.1f} "
                  f"{r['temp'] if r['temp'] is not None else '-':>5} {r['hum'] if r['hum'] is not None else '-':>5}  "
                  f"{leak:>4}  "
                  f"[{'OK' if st['sensors'] else '--'} / {'OK' if st['imu'] else '--'} / "
                  f"{'OK' if st['leak'] else '--'}]")
        for e in ps.errors:
            print("ERROR", e)
        ps.errors.clear()

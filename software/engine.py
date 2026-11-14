"""Device engine: runs the models on the live pod stream and keeps today's history.

Produces one JSON-able `state` dict per second for the parent app:
  now   - recommendation, diaper fill, chance it's full within 1 h, time to change, asleep
  today - healthy diaper time, hydration (wee count vs expected), gut regularity (poo count),
          rash risk (time in soiled / over-full diaper), sleep, changes
  timeline + current-diaper chart series

Events are detected from the model outputs:
  wee    - the diaper's fill high-water mark jumps by > 4 %
  poo    - diaper state becomes "soiled"
  change - "I changed the diaper" button, or the sensor reads dry again after being wet
  sleep  - Nicla IMU still for most of the last 10 min
Activity: every minute is classed asleep / calm / active from the IMU, which feeds
the day strip, sleep stretches, naps and "wake-ups after a wet or dirty diaper".
DHT11 (about ±2 °C, ±5 % RH), two placements:
  nappy (default) - inside the nappy at the waistband: skin-side microclimate. Long spells above
                    85 % RH add to the rash-risk score (thresholds are ASSUMPTIONS to tune on the bench).
  room            - near the cot: checked against the safe-sleep range of 16-20 °C (The Lullaby Trust).
These are wellness indicators, not medical measurements.
"""
import os
import threading
import time
import traceback
from collections import deque

import joblib
import numpy as np
import pandas as pd

import config as C
from features import compute_features
from recommend import recommend
from sensor import load_cal, still_threshold
from simulate import SimPod

AGE_TEXT = {"0-3m": "2 months", "3-6m": "4 months", "6-12m": "9 months"}
WEE_JUMP = 0.04          # fill high-water mark jump that counts as a wee
WEE_REFRACTORY_S = 180
HUMID_RH = 85            # ASSUMPTION: inside-nappy humidity treated as "humid"
HUMID_LONG_MIN = 30      # ASSUMPTION: humid this long in one diaper -> higher irritation risk


def _new_session(t):
    return {"start": t, "end": None, "wees": 0, "poo_t": None, "soiled_s": 0.0,
            "overfull_s": 0.0, "wet_s": 0.0, "humid_s": 0.0, "max_sat": 0.0, "last_wee_t": -1e12}


class Engine:
    def __init__(self, mode="sim", ports=None, age="3-6m", size=3, baby="Ava", seed_history=True, climate="nappy",
                 models_path="models.joblib"):
        self.climate_mode = climate
        self.cal = load_cal()
        self.M = joblib.load(models_path)
        self.mode, self.age, self.size, self.baby = mode, age, size, baby
        self.lock = threading.RLock()
        self.t0 = time.time()
        self.clock = self.t0
        self._inbox, self.meta = deque(), {}           # external mode: rows pushed in by the inference service
        if mode == "sim":
            self.pod, self.speed, self.ps = SimPod(self.cal, age=age, size=size, climate=climate, worn=True), 60, None
        elif mode == "external":
            self.pod, self.speed, self.ps = None, 1, None
        else:
            from pod_stream import PodStream
            self.pod, self.speed, self.ps = None, 1, PodStream(ports)
        self.buf, self.events, self.sessions = [], [], []
        self.cur = _new_session(self.clock)
        self.sleep_s, self.asleep, self.seeded = 0.0, None, False
        self.minute_levels, self._macc = {}, {}      # minute -> 0 asleep / 1 calm / 2 active
        self.minute_temp, self._tacc = {}, {}        # minute -> mean room temperature
        self._env = deque(maxlen=120)                # recent (temp, hum) readings
        i = self.cal["imu"]
        self.calm_thr = float(np.sqrt(still_threshold(self.cal) * i["active_acc_std"]))
        self.day_start = self.clock
        self.state, self.err = {"ready": False}, None
        if seed_history:
            self._seed_history()
        threading.Thread(target=self._loop, daemon=True).start()

    # ------------------------------------------------------------------ demo history
    def _seed_history(self, hours=9.0):
        """Simulated 'earlier today' so the day metrics are populated for a demo."""
        rng = np.random.default_rng(11)
        g = C.AGE_GROUPS[self.age]
        cap = C.DIAPER_CAPACITY_ML[self.size]
        start = self.clock - hours * 3600
        self.day_start = start
        # sleep / wake blocks
        t, asleep = start, True
        while t < self.clock - 600:
            dur = rng.uniform(40, 130) * 60 if asleep else rng.uniform(45, 110) * 60
            end = min(t + dur, self.clock - 600)
            if asleep:
                self.sleep_s += end - t
            m0, m1 = int(t // 60), int(end // 60)
            lvl = 1
            for m in range(m0, m1):
                if not asleep and rng.random() < 1 / 10:     # bouts of several minutes
                    lvl = int(rng.choice([1, 2], p=[0.6, 0.4]))
                self.minute_levels[m] = 0 if asleep else lvl
            self.events.append({"t": t, "type": "sleep" if asleep else "wake"})
            t, asleep = end, not asleep
        for m in range(int(start // 60), int(self.clock // 60)):        # room: cool overnight, warmer by day
            h = ((m * 60 - start) / 3600)
            self.minute_temp[m] = 18.8 + 1.2 * np.sin(h / hours * np.pi) + rng.normal(0, 0.15)
        self.asleep = not asleep  # state of the last block
        for m in range(int((self.clock - 600) // 60), int(self.clock // 60)):
            self.minute_levels[m] = 0 if self.asleep else 1
        # diapers
        t = start
        while t < self.clock - 1800:
            end = min(t + rng.uniform(2.0, 3.4) * 3600, self.clock - 900)
            s = _new_session(t)
            self.events.append({"t": t, "type": "change"})
            if rng.random() < g["poo_per_day"] * (end - t) / 86400 and sum(x["poo_t"] is not None for x in self.sessions) < 2:
                pt = rng.uniform(t + 1200, end)
                s["poo_t"] = pt
                s["soiled_s"] = rng.uniform(4, 12) * 60
                end = min(end, pt + s["soiled_s"])
                self.events.append({"t": pt, "type": "poo"})
            vol, last, tt = 0.0, t, t + rng.uniform(10, 40) * 60
            sat_prev = 0.0
            while tt < end:
                dt = tt - last
                s["wet_s"] += dt if sat_prev >= 0.5 else 0
                s["overfull_s"] += dt if sat_prev >= C.LEAK_SAT else 0
                vol += max(5.0, rng.normal(*g["void_ml"]))
                sat_prev = min(1.0, vol / cap)
                s["wees"] += 1
                s["max_sat"] = sat_prev
                self.events.append({"t": tt, "type": "wee"})
                last = tt
                tt += max(600, rng.gamma(4, g["void_interval_min"] * 60 / 4))
            s["wet_s"] += (end - last) if sat_prev >= 0.5 else 0
            s["humid_s"] = s["wet_s"] * 0.5
            s["overfull_s"] += (end - last) if sat_prev >= C.LEAK_SAT else 0
            s["end"] = end
            self.sessions.append(s)
            t = end
        self.events.append({"t": t, "type": "change"})
        self.cur = _new_session(self.clock)
        self.seeded = True

    # ------------------------------------------------------------------ actions
    def action(self, name, value=None):
        with self.lock:
            if name == "change":
                self._change()
            elif self.mode == "sim" and name == "wee":
                self.pod.wee()
            elif self.mode == "sim" and name == "poo":
                self.pod.poo()
            elif self.mode == "sim" and name == "asleep":
                self.pod.asleep = bool(value)
            elif self.mode == "sim" and name == "room_temp":
                self.pod.room_temp = float(np.clip(float(value), 10, 35))
            elif self.mode == "sim" and name == "speed":
                self.speed = int(np.clip(int(value), 1, 600))
            else:
                return False
            return True

    def _change(self):
        self.cur["end"] = self.clock
        self.sessions.append(self.cur)
        self.events.append({"t": self.clock, "type": "change"})
        self.cur = _new_session(self.clock)
        self.buf = []
        if self.pod:
            self.pod.change()

    # ------------------------------------------------------------------ main loop
    def _loop(self):
        while True:
            try:
                self.tick()
                self.err = None
            except Exception as e:  # noqa: BLE001
                self.err = str(e)
                traceback.print_exc()
            time.sleep(1)

    def feed(self, rows, meta=None):
        """External mode: rows (dicts with t, moisture, gas, acc_*, gyro_mean, temp, hum) from the gateway."""
        with self.lock:
            self._inbox.extend(rows)
            if meta:
                self.meta = meta

    def reload_models(self, path="models.joblib"):
        with self.lock:
            self.M = joblib.load(path)

    def _read(self):
        if self.mode == "external":
            rows = list(self._inbox)
            self._inbox.clear()
            return rows
        if self.pod:
            rows = [self.pod.step() for _ in range(self.speed)]
            for r in rows:
                r["t"] = self.t0 + r["t"]
            return rows
        return self.ps.read()

    def tick(self):
        with self.lock:
            rows = self._read()
            self.buf.extend(rows)
            self.buf = self.buf[-14 * 3600:]
            if self.buf:
                self.clock = self.buf[-1]["t"]
            self._climate(rows)
            if len(self.buf) < 30 or not rows:
                self.state = self._build(None)
                return
            df = pd.DataFrame(self.buf)
            f = compute_features(df, self.cal, self.size, step=1)
            new = f.iloc[-len(rows):]
            ts = df["t"].to_numpy()[-len(rows):]
            self._activity(ts, new["still_frac10"].to_numpy(), df["acc_std"].to_numpy()[-len(rows):])
            last = f.iloc[[-1]]
            F = self.M["features"]

            probs = self.M["state"].predict_proba(last[F["state"]])[0]
            state = self.M["states"][int(self.M["state"].classes_[int(np.argmax(probs))])]
            p_full = float(self.M["leak"].predict_proba(last[F["leak"]])[0, 1])
            ttl = float(np.clip(np.expm1(self.M["ttl"].predict(last[F["ttl"]])[0]), 0, C.TTL_CAP_MIN))
            sat = float(last["sat"].iloc[0])
            still_frac = float(last["still_frac10"].iloc[0])

            # accumulate exposure
            s = self.cur
            sats = new["sat"].to_numpy()
            s["wet_s"] += float((sats >= 0.5).sum())
            s["overfull_s"] += float((sats >= C.LEAK_SAT).sum())
            # wees: high-water mark jumps
            for t_, m in zip(ts, new["sat_max"].to_numpy()):
                if m > s["max_sat"] + WEE_JUMP and t_ - s["last_wee_t"] > WEE_REFRACTORY_S:
                    s["wees"] += 1
                    s["last_wee_t"] = t_
                    self.events.append({"t": float(t_), "type": "wee"})
                s["max_sat"] = max(s["max_sat"], float(m))
            # poo
            if state == "soiled":
                if s["poo_t"] is None:
                    s["poo_t"] = self.clock
                    self.events.append({"t": self.clock, "type": "poo"})
                s["soiled_s"] += len(rows)
            # sleep
            asleep = still_frac > 0.8
            if self.asleep is not None and asleep != self.asleep:
                self.events.append({"t": self.clock, "type": "sleep" if asleep else "wake"})
            self.asleep = asleep
            if asleep:
                self.sleep_s += len(rows)
            # auto change: wet diaper -> sensor reads dry again
            if s["max_sat"] > 0.3 and len(sats) and float(f["sat"].iloc[-20:].max()) < 0.05:
                self._change()
                self.state = self._build(None)
                return

            now = {"state": state, "probs": {self.M["states"][int(k)]: float(p)
                                             for k, p in zip(self.M["state"].classes_, probs)},
                   "sat": sat, "p_full_1h": p_full, "ttl_min": ttl, "asleep": asleep,
                   "moving": bool(self.M["act"].predict(last[F["act"]])[0])}
            title, detail, color = recommend(state, sat, p_full, ttl, still_frac)
            now["rec"] = {"title": title, "detail": detail, "level": color}
            step = max(1, len(f) // 200)
            series = {"mins": f["mins"].iloc[::step].round(1).tolist(),
                      "sat": f["sat"].iloc[::step].round(3).tolist(),
                      "gas": f["gas_rise"].iloc[::step].round(3).tolist()}
            self.state = self._build(now, series)

    def _activity(self, ts, still_frac, acc_std):
        lvl = np.where(still_frac > 0.8, 0, np.where(acc_std < self.calm_thr, 1, 2))
        for t_, l in zip(ts, lvl):
            self._macc.setdefault(int(t_ // 60), [0, 0, 0])[int(l)] += 1
        cur = int(self.clock // 60)
        for m in [m for m in self._macc if m < cur]:
            self.minute_levels[m] = int(np.argmax(self._macc.pop(m)))

    def _climate(self, rows):
        for r in rows:
            t_, h_ = r.get("temp"), r.get("hum")
            if t_ is None or h_ is None or not np.isfinite(t_) or not np.isfinite(h_):
                continue
            self._env.append((t_, h_))
            if self.climate_mode == "nappy" and h_ > HUMID_RH:
                self.cur["humid_s"] += 1
            a = self._tacc.setdefault(int(r["t"] // 60), [0.0, 0])
            a[0] += t_
            a[1] += 1
        cur = int(self.clock // 60)
        for m in [m for m in self._tacc if m < cur]:
            tot, k = self._tacc.pop(m)
            self.minute_temp[m] = tot / k

    def _climate_state(self):
        if not self._env:
            return {"connected": False, "mode": self.climate_mode}
        temp = float(np.median([e[0] for e in list(self._env)[-30:]]))
        hum = float(np.median([e[1] for e in list(self._env)[-30:]]))
        if self.climate_mode == "nappy":
            cur_min = self.cur["humid_s"] / 60
            today_min = sum(x["humid_s"] for x in self.sessions + [self.cur]) / 60
            if hum < 70:
                st = ("ok", "Dry", "Skin side is dry and comfortable.")
            elif hum < HUMID_RH:
                st = ("watch", "Getting humid", "Humidity against the skin is rising.")
            elif cur_min >= HUMID_LONG_MIN:
                st = ("alert", "Humid for a while", f"Above {HUMID_RH}% for {cur_min:.0f} min. Damp skin is more prone to irritation, so a change will help.")
            else:
                st = ("watch", "Humid", f"Above {HUMID_RH}% for {cur_min:.0f} min in this diaper.")
            return {"connected": True, "mode": "nappy", "temp": temp, "hum": hum, "level": st[0], "label": st[1],
                    "detail": st[2], "humid_min_diaper": cur_min, "humid_min_today": today_min}
        if temp < 16:
            st = ("watch", "Cool", "A bit cool. Add a layer or use a warmer sleep bag.")
        elif temp <= 20:
            st = ("ok", "Comfortable", "Within the safe-sleep range of 16–20°C.")
        elif temp <= 24:
            st = ("watch", "A bit warm", "Above 16–20°C. Use lighter bedding or fewer layers.")
        else:
            st = ("alert", "Too warm", "Overheating raises SIDS risk. Open a door or window and remove layers.")
        hum_note = " Air is dry." if hum < 30 else (" Air is humid." if hum > 70 else "")
        m0 = int(self.day_start // 60)
        asleep_m = [m for m, l in self.minute_levels.items() if l == 0 and m >= m0 and m in self.minute_temp]
        in_range = (sum(16 <= self.minute_temp[m] <= 20 for m in asleep_m) / len(asleep_m)) if asleep_m else None
        return {"connected": True, "mode": "room", "temp": temp, "hum": hum, "level": st[0], "label": st[1],
                "detail": st[2] + hum_note, "sleep_in_range": in_range}

    def _sleep_stats(self):
        """Sleep stretches from minute levels (awake gaps < 3 min don't break a stretch)."""
        if not self.minute_levels:
            return {"runs": [], "sleep_min": 0, "calm_min": 0, "active_min": 0, "naps": 0,
                    "longest_min": 0, "wakes": [], "current": None}
        m0 = int(self.day_start // 60)
        mins = [m for m in sorted(self.minute_levels) if m >= m0]
        lv = [self.minute_levels[m] for m in mins]
        runs = []                                   # day strip: merged runs of same level
        for m, l in zip(mins, lv):
            if runs and runs[-1]["level"] == l and m == runs[-1]["end"]:
                runs[-1]["end"] = m + 1
            else:
                runs.append({"start": m, "end": m + 1, "level": l})
        asleep = [l == 0 for l in lv]
        i, n = 0, len(asleep)                       # fill short awake gaps inside sleep
        while i < n:
            if not asleep[i]:
                j = i
                while j < n and not asleep[j]:
                    j += 1
                if 0 < i and j < n and j - i < 3:
                    for k in range(i, j):
                        asleep[k] = True
                i = j
            else:
                i += 1
        stretches, start = [], None
        for k, a in enumerate(asleep + [False]):
            if a and start is None:
                start = k
            elif not a and start is not None:
                if k - start >= 10:
                    stretches.append((mins[start], mins[k - 1] + 1))
                start = None
        wakes = [e for _, e in stretches if e < mins[-1]]
        cur_state = "asleep" if asleep[-1] else "awake"
        k = n - 1
        while k > 0 and asleep[k - 1] == asleep[-1]:
            k -= 1
        return {
            "runs": [{"start": r["start"] * 60, "end": r["end"] * 60, "level": r["level"]} for r in runs],
            "sleep_min": sum(l == 0 for l in lv), "calm_min": sum(l == 1 for l in lv),
            "active_min": sum(l == 2 for l in lv), "naps": len(stretches),
            "longest_min": max([e - s_ for s_, e in stretches], default=0),
            "wakes": [w * 60 for w in wakes],
            "current": {"state": cur_state, "minutes": n - k},
        }

    # ------------------------------------------------------------------ metrics
    def _build(self, now, series=None):
        g = C.AGE_GROUPS[self.age]
        sess = self.sessions + [self.cur]
        span = max(60.0, self.clock - self.day_start)
        span_h = span / 3600
        soiled = sum(x["soiled_s"] for x in sess)
        overfull = sum(x["overfull_s"] for x in sess)
        wet = sum(x["wet_s"] for x in sess)
        healthy = float(np.clip(1 - (soiled + overfull) / span, 0, 1))

        ev = sorted(self.events, key=lambda e: e["t"])
        wees = sum(e["type"] == "wee" for e in ev)
        poos = [e["t"] for e in ev if e["type"] == "poo"]
        wakeups = sum(e["type"] == "wake" for e in ev)
        changes = len(self.sessions)

        exp_rate = 60 / (g["void_interval_min"] * 1.2)        # wees per hour, mix of sleep/awake
        exp = exp_rate * span_h
        if span_h < 3:
            hyd = ("ok", "Tracking", "Needs a few hours of data")
        elif wees >= 0.7 * exp:
            hyd = ("ok", "On track", f"{wees} wet so far, about {exp * 0.7:.0f}–{exp * 1.3:.0f} expected by now")
        elif wees >= 0.5 * exp:
            hyd = ("watch", "A little low", f"{wees} wet so far, about {exp * 0.7:.0f}–{exp * 1.3:.0f} expected. Offer a feed.")
        else:
            hyd = ("alert", "Fewer wet diapers than usual",
                   f"Only {wees} wet so far. If this continues, talk to your doctor.")

        poo_int_h = 24 / g["poo_per_day"]
        last_poo_h = (self.clock - poos[-1]) / 3600 if poos else None
        if poos and len(poos) > 3 * g["poo_per_day"] * max(span_h, 6) / 24:
            gut = ("watch", "More often than usual", "Keep an eye out for runny stools")
        elif last_poo_h is not None and last_poo_h < 2.5 * poo_int_h:
            gut = ("ok", "Regular", f"Last poo {self._ago(last_poo_h)}")
        elif last_poo_h is None and span_h < 2.5 * poo_int_h:
            gut = ("ok", "Regular", "No poo yet today")
        else:
            gut = ("watch", "Less often than usual", "No poo for a while. Mention it if it continues.")

        soiled_min = soiled / 60
        humid_min = sum(x["humid_s"] for x in sess) / 60 if self.climate_mode == "nappy" else 0.0
        risk_score = soiled_min + 0.3 * overfull / 60 + 0.1 * humid_min
        rash = ("ok", "Low") if risk_score < 20 else (("watch", "Medium") if risk_score < 45 else ("alert", "High"))

        mins_in = (self.clock - self.cur["start"]) / 60
        avg_diaper = np.mean([(x["end"] - x["start"]) / 60 for x in self.sessions]) if self.sessions else None
        labels = {"wee": "Wee", "poo": "Poo", "change": "Diaper changed", "sleep": "Fell asleep", "wake": "Woke up"}
        timeline = [{"t": e["t"], "type": e["type"], "label": labels[e["type"]]} for e in ev[::-1][:14]]

        sl = self._sleep_stats()
        diaper_ev = [e["t"] for e in ev if e["type"] in ("wee", "poo")]
        wet_wakes = sum(any(w - 600 <= t_ <= w + 60 for t_ in diaper_ev) for w in sl["wakes"])
        sleep_out = {k: sl[k] for k in ("sleep_min", "calm_min", "active_min", "naps", "longest_min", "current")}
        sleep_out.update(wakeups=len(sl["wakes"]), wet_wakeups=wet_wakes, runs=sl["runs"],
                         markers=[{"t": e["t"], "type": e["type"]} for e in ev
                                  if e["type"] in ("wee", "poo", "change") and e["t"] >= self.day_start])
        if self.mode == "sim":
            conn, mode_out, speed_out = {"sensors": True, "imu": True}, "sim", self.speed
            sim_out = {"asleep": self.pod.asleep}
            room_out = self.pod.room_temp
        elif self.mode == "external":
            conn = self.meta.get("status", {"sensors": False, "imu": False})
            mode_out, speed_out = self.meta.get("mode", "serial"), self.meta.get("speed", 1)
            sim_out = self.meta.get("sim")
            room_out = (sim_out or {}).get("room_temp")
        else:
            conn, mode_out, speed_out, sim_out, room_out = self.ps.status(), "serial", 1, None, None
        return {
            "ready": now is not None, "error": self.err, "mode": mode_out, "speed": speed_out,
            "clock": self.clock, "baby": {"name": self.baby, "age": AGE_TEXT[self.age], "size": self.size},
            "connected": conn, "demo_history": self.seeded,
            "calibrated": os.path.exists("calibration.json"),
            "sim": sim_out,
            "now": now, "series": series,
            "diaper": {"minutes": mins_in, "wees": self.cur["wees"]},
            "today": {
                "hours": span_h, "healthy_pct": healthy,
                "soiled_min": soiled_min, "overfull_min": overfull / 60, "wet_min": wet / 60, "humid_min": humid_min,
                "rash": {"level": rash[0], "label": rash[1]},
                "wees": wees, "wees_expected": exp,
                "hydration": {"level": hyd[0], "label": hyd[1], "detail": hyd[2]},
                "poos": len(poos), "gut": {"level": gut[0], "label": gut[1], "detail": gut[2]},
                "sleep_min": self.sleep_s / 60, "wakeups": wakeups,
                "changes": changes, "avg_diaper_min": avg_diaper,
            },
            "timeline": timeline,
            "sleep": sleep_out, "day_start": self.day_start, "climate": self._climate_state(),
            "sim_room_temp": room_out,
        }

    @staticmethod
    def _ago(h):
        return f"{h * 60:.0f} min ago" if h < 1 else f"{h:.1f} h ago"

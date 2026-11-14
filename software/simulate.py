"""Synthetic data generator.

Simulates diaper "sessions" (from one change to the next) at 1 Hz:
baby activity (asleep / calm / active), wees and poos from config.py,
then passes the true state through a sensor model scaled by calibration.json,
so the output looks like what YOUR firmware sends.

Columns match the firmware: t, moisture, gas, acc_mean, acc_std, gyro_mean
plus ground-truth labels: state, moving, sat_true, ttl_min.

Run directly to dump a sample:  python simulate.py --sessions 5 --out sample.csv
"""
import argparse

import numpy as np
import pandas as pd

import config as C
from sensor import load_cal, raw_from_sat

ACT = ["asleep", "calm", "active"]
ACT_LEVEL = np.array([0.0, 0.55, 1.0])   # position between still and active (log scale)


# ---------- sensor models (shared by batch sim and the live SimPod) ----------
def void_spike(dt, amp, tau=90.0):
    """Surface wetness jumps when urine hits the sensor, then soaks into the core.
    tau = absorption time constant (s): faster-absorbing products have a smaller tau."""
    dt = np.asarray(dt, float)
    d = np.clip(dt, 0, None)
    return np.where(dt >= 0, amp * (1 - np.exp(-d / 3.0)) * np.exp(-d / tau), 0.0)


def ammonia(sat_true, dt_since_first_void):
    """Slow ammonia build-up from urine."""
    dt = np.clip(np.asarray(dt_since_first_void, float), 0, None)
    return 0.10 * np.minimum(sat_true, 1.0) * (1 - np.exp(-dt / 1800.0))


def poo_gas(dt, amp):
    dt = np.asarray(dt, float)
    d = np.clip(dt, 0, None)
    return np.where(dt >= 0, amp * (1 - np.exp(-d / 40.0)) * np.exp(-d / 43200.0), 0.0)


def moisture_raw(sat_sensor, cal, rng):
    sat_sensor = np.asarray(sat_sensor, float)
    return raw_from_sat(np.clip(sat_sensor, 0, 1), cal) + rng.normal(0, cal["moisture"]["noise"], sat_sensor.shape)


def gas_raw(g_norm, cal, rng):
    g = cal["gas"]
    g_norm = np.asarray(g_norm, float)
    return g["base"] + g_norm * (g["voc"] - g["base"]) + rng.normal(0, g["noise"], g_norm.shape)


def imu(act, cal, rng):
    """act: array of 0/1/2 -> (acc_mean, acc_std, gyro_mean)."""
    act = np.asarray(act)
    i = cal["imu"]
    lvl = ACT_LEVEL[act]
    acc_std = np.exp(np.log(i["still_acc_std"]) + lvl * np.log(i["active_acc_std"] / i["still_acc_std"])
                     + rng.normal(0, 0.35, act.shape))
    gyro = np.exp(np.log(i["still_gyro"]) + lvl * np.log(i["active_gyro"] / i["still_gyro"])
                  + rng.normal(0, 0.35, act.shape))
    twitch = (act == 0) & (rng.random(act.shape) < 0.01)      # sleep twitches
    acc_std = np.where(twitch, acc_std * rng.uniform(3, 10, act.shape), acc_std)
    gyro = np.where(twitch, gyro * rng.uniform(3, 10, act.shape), gyro)
    acc_mean = 1.0 + rng.normal(0, 0.01, act.shape) + acc_std * rng.normal(0, 0.3, act.shape)
    return acc_mean, acc_std, gyro


# ---------- batch simulation ----------
def _activity_track(rng, T, start_asleep):
    act = np.empty(T, np.int8)
    s = 0 if start_asleep else int(rng.choice([1, 2]))
    t = 0
    while t < T:
        d = int(max(60, rng.exponential(C.ACTIVITY_MEAN_MIN[ACT[s]] * 60)))
        act[t:t + d] = s
        t += d
        s = 1 if s != 1 else int(rng.choice([0, 2]))
    return act


def simulate_session(rng, cal, age=None, size=None, kind=None):
    """One diaper session (day ~3 h or night ~10 h). Simulates 6 h beyond the
    session so the time-to-leak label is known, but returns only the session."""
    age = age or str(rng.choice(list(C.AGE_GROUPS)))
    g = C.AGE_GROUPS[age]
    size = size or int(rng.choice(g["sizes"]))
    kinds = list(C.SESSION_TYPES)
    kind = kind or str(rng.choice(kinds, p=[C.SESSION_TYPES[k][0] for k in kinds]))
    hours = C.SESSION_TYPES[kind][1] * rng.uniform(0.8, 1.2)
    T, keep = int((hours + 6) * 3600), int(hours * 3600)
    t = np.arange(T)
    if kind == "night":   # mostly asleep, short wake-ups
        act = np.zeros(T, np.int8)
        for _ in range(rng.poisson(hours / 3)):
            s0 = int(rng.uniform(0, keep)); act[s0:s0 + int(rng.uniform(300, 1500))] = 1
    else:
        act = _activity_track(rng, T, rng.random() < 0.3)

    # wees
    cap = C.DIAPER_CAPACITY_ML[size] * rng.uniform(0.85, 1.15)
    wetter = rng.lognormal(0, 0.4)          # some babies wee a lot more than others
    vol = np.zeros(T)
    voids = []
    tt = rng.uniform(0.1, 1.0) * g["void_interval_min"] * 60
    while tt < T:
        i = int(tt)
        vol[i] += max(5.0, rng.normal(*g["void_ml"]) * wetter)
        voids.append((i, rng.uniform(0.15, 0.35)))
        mean = g["void_interval_min"] * 60 * (C.SLEEP_VOID_INTERVAL_FACTOR if act[i] == 0 else 1)
        tt += max(600, rng.gamma(4, mean / 4))
    sat_true = np.minimum(1.05, np.cumsum(vol) / cap)

    spike = np.zeros(T)
    for i, amp in voids:
        j = min(T, i + 900)
        spike[i:j] += void_spike(t[i:j] - i, amp)
    sat_sensor = sat_true * rng.uniform(0.7, 1.2) + spike     # placement / contact varies
    # confounder: sensor loses contact when baby moves a lot
    loss = (act == 2) & (rng.random(T) < 0.3)
    sat_sensor = np.where(loss, sat_sensor * rng.uniform(0.5, 0.9, T), sat_sensor)
    sat_sensor = np.clip(pd.Series(sat_sensor).rolling(5, min_periods=1).mean().to_numpy(), 0, 1)

    # poo (amplitude varies a lot - small poos are hard to smell)
    pt = rng.exponential(86400 / g["poo_per_day"])
    poo_t = int(pt) if pt < T else None

    # gas
    gn = np.full(T, rng.normal(0, 0.05))                      # unit-to-unit baseline offset
    gn += np.cumsum(rng.normal(0, 0.0002, T))                 # slow drift
    if voids:
        gn += ammonia(sat_true, t - voids[0][0]) * rng.uniform(0.5, 2.0)
    if poo_t is not None:
        gn += poo_gas(t - poo_t, rng.uniform(0.25, 1.3))
    # confounder: room smells (food, perfume, cleaning spray) - transient VOC bumps
    for _ in range(rng.poisson(T / 3600 * 0.4)):
        s0, dur = int(rng.uniform(0, T)), rng.uniform(120, 900)
        dt = t[s0:] - s0
        gn[s0:] += rng.uniform(0.1, 0.6) * (1 - np.exp(-dt / 30)) * np.exp(-dt / dur)

    acc_mean, acc_std, gyro = imu(act, cal, rng)

    # labels
    state = np.zeros(T, np.int8)
    if voids:
        state[voids[0][0] + 5:] = 1
    if poo_t is not None:
        state[min(T, poo_t + 45):] = 2
    over = np.nonzero(sat_true >= C.LEAK_SAT)[0]
    ttl = (np.full(T, float(C.TTL_CAP_MIN)) if len(over) == 0
           else np.clip((over[0] - t) / 60.0, 0, C.TTL_CAP_MIN))

    df = pd.DataFrame({
        "t": t, "moisture": moisture_raw(sat_sensor, cal, rng), "gas": gas_raw(gn, cal, rng),
        "acc_mean": acc_mean, "acc_std": acc_std, "gyro_mean": gyro,
        "state": state, "moving": (act > 0).astype(np.int8),
        "sat_true": np.minimum(sat_true, 1.0), "ttl_min": ttl,
    }).iloc[:keep]
    return df, {"age": age, "size": size, "kind": kind, "hours": round(hours, 1),
                "capacity_ml": round(cap), "n_voids": len(voids), "poo_t": poo_t}


# ---------- live simulated pod (for demos without hardware) ----------
class SimPod:
    """Interactive pod: press wee()/poo()/squeeze(), set .asleep, call step() each second.

    Product parameters (used for Product Lab examples):
      capacity_ml - wet-zone capacity under the sensor; absorb_tau - absorption time constant (s)
      rewet       - how much surface wetness comes back when the nappy is pressed
      breath      - breathability: how fast humidity inside the nappy falls again
    climate: "room" (DHT11 measures the room) or "nappy" (DHT11 inside the nappy)
    worn:    True for the parent-app demo (skin-warm inside), False for bench tests
    """

    def __init__(self, cal, age="3-6m", size=3, seed=None, capacity_ml=None, absorb_tau=90.0,
                 rewet=0.25, breath=1.0, climate="room", worn=True):
        self.cal, self.age, self.size = cal, age, size
        self.rng = np.random.default_rng(seed)
        self.capacity_ml, self.absorb_tau, self.rewet_k, self.breath = capacity_ml, absorb_tau, rewet, breath
        self.climate, self.worn = climate, worn
        self.asleep, self.auto, self.pressing = True, False, False
        self.t = 0
        self._awake_lvl = 1
        self.room_temp, self._temp, self._hum = 19.0, 19.0, 50.0
        self.change()

    def change(self):
        self.vol, self.voids, self.poo_t, self.first_void = 0.0, [], None, None
        base = self.capacity_ml or C.DIAPER_CAPACITY_ML[self.size]
        self.cap = base * (1.0 if self.capacity_ml else self.rng.uniform(0.9, 1.1))
        self.gain = 1.0 if self.capacity_ml else self.rng.uniform(0.9, 1.1)
        self.offset = self.rng.normal(0, 0.03)
        if self.climate == "nappy":
            self._hum = 55.0

    def wee(self, ml=None):
        g = C.AGE_GROUPS[self.age]
        self.vol += ml or max(5.0, self.rng.normal(*g["void_ml"]))
        self.voids.append((self.t, self.rng.uniform(0.15, 0.35)))
        if self.first_void is None:
            self.first_void = self.t

    def poo(self):
        if self.poo_t is None:
            self.poo_t = self.t

    def step(self):
        if self.auto:
            g = C.AGE_GROUPS[self.age]
            interval = g["void_interval_min"] * 60 * (C.SLEEP_VOID_INTERVAL_FACTOR if self.asleep else 1)
            if self.rng.random() < 1 / interval:
                self.wee()
            if self.rng.random() < g["poo_per_day"] / 86400:
                self.poo()
        sat_true = min(1.05, self.vol / self.cap)
        win = max(900, 6 * self.absorb_tau)
        spike = sum(float(void_spike(self.t - i, a, self.absorb_tau)) for i, a in self.voids if self.t - i < win)
        if self.pressing:                           # pressure squeezes fluid back to the surface (rewet)
            spike += self.rewet_k * min(1.0, sat_true)
        sat_sensor = np.clip(sat_true * self.gain + spike, 0, 1)
        gn = self.offset
        if self.first_void is not None:
            gn += float(ammonia(sat_true, self.t - self.first_void))
        if self.poo_t is not None:
            gn += float(poo_gas(self.t - self.poo_t, 1.0))
        if self.rng.random() < 1 / 180:            # awake activity comes in bouts of a few minutes
            self._awake_lvl = int(self.rng.choice([1, 2], p=[0.6, 0.4]))
        act = np.array([0 if self.asleep else self._awake_lvl])
        am, asd, gy = imu(act, self.cal, self.rng)
        if self.climate == "nappy":
            t_target = self.room_temp + (14.5 if self.worn else 0.0)       # skin-warm when worn
            h_target = min(97.0, 55 + (8 if self.worn else 0) + 38 * min(1.0, sat_true * 1.4))
            tau_h = 60 if h_target > self._hum else 600 / self.breath      # rises fast, dries slowly
            self._temp += (t_target - self._temp) / 300 + self.rng.normal(0, 0.003)
            self._hum += (h_target - self._hum) / tau_h + self.rng.normal(0, 0.02)
        else:
            self._temp += (self.room_temp - self._temp) / 900 + self.rng.normal(0, 0.003)   # room drifts slowly
            self._hum += (50 - (self._temp - 19) * 2 - self._hum) / 900 + self.rng.normal(0, 0.01)
        row = {"t": float(self.t),
               "temp": round(self._temp + self.rng.normal(0, 0.1), 1), "hum": round(min(99.0, self._hum + self.rng.normal(0, 0.3)), 1),
               "moisture": float(moisture_raw(np.array([sat_sensor]), self.cal, self.rng)[0]),
               "gas": float(gas_raw(np.array([gn]), self.cal, self.rng)[0]),
               "acc_mean": float(am[0]), "acc_std": float(asd[0]), "gyro_mean": float(gy[0])}
        self.t += 1
        return row


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", type=int, default=5)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="synthetic_sample.csv")
    a = ap.parse_args()
    cal = load_cal()
    rng = np.random.default_rng(a.seed)
    parts = []
    for k in range(a.sessions):
        df, meta = simulate_session(rng, cal)
        df["session"], df["age"], df["size"] = k, meta["age"], meta["size"]
        parts.append(df)
        print(f"session {k}: {meta}")
    pd.concat(parts).to_csv(a.out, index=False)
    print(f"wrote {a.out}")

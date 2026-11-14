"""Record a labelled bench run on a real nappy (P&G brief: bench tests only, no people).

Each run follows a timed protocol. You press Enter at the moment you do something
(pour fluid, place the poo proxy, start pressing...), so every row is labelled with
the TRUE state of the nappy and the exact event times. These runs are the
"labelled bench dataset" used by validate.py and the Product Lab page.

  python bench.py insult   --nappy "Brand A size 3"          (boards auto-detected; or --port COM3 --port COM4)
  python bench.py movement --nappy "Brand A size 3"
  python bench.py poo      --nappy "Brand A size 3"

Protocols
  insult    2 min dry baseline, then N fluid insults (default 4 x 30 mL, 5 min apart),
            then an optional press test for rewet (--press). Use saline (0.9 % salt water)
            at body temperature if you can - it behaves more like urine than tap water.
  movement  NO fluid. 1 min still, 3 min moving/flexing the nappy with the Nicla attached,
            1 min still. Any "wet" or "poo" alert here is a false alarm. Add --prewet if the
            nappy already has fluid in it (truth = wet).
  poo       2 min baseline, then place the poo proxy (vinegar/alcohol swab, or a small amount
            of food paste) next to the gas sensor, then 5 min.

Sensor placement: note where you put the moisture sensor (--placement), e.g.
"under topsheet", "outside backsheet". Comparing placements is itself a good experiment.

Test without hardware:  python bench.py insult --simulate --auto --nappy "test"
Output: bench/<nappy>_<protocol>_<time>.csv
"""
import argparse
import csv
import os
import queue
import re
import sys
import threading
import time

from pod_stream import COLS

FIELDS = ["host_time", "run_id", "nappy", "placement", "protocol", "source", "phase", "truth", "event", "volume_ml"] + COLS


def protocol_steps(a):
    """List of steps: ("wait", seconds, phase) or ("do", prompt, event, phase_after, truth_after)."""
    if a.protocol == "insult":
        steps = [("wait", a.baseline, "baseline")]
        for i in range(1, a.insults + 1):
            steps += [("do", f"Pour {a.volume:g} mL of test fluid onto the nappy now, then press Enter.",
                       f"insult{i}", f"after_insult{i}", "wet"),
                      ("wait", a.wait, f"after_insult{i}")]
        if a.press:
            steps += [("do", "Press firmly on the wet area (hand or ~2 kg weight). Press Enter as you START.",
                       "press_start", "pressing", "wet"),
                      ("wait", 30, "pressing"),
                      ("do", "Release the pressure and press Enter.", "press_end", "after_press", "wet"),
                      ("wait", 60, "after_press")]
        return steps
    if a.protocol == "movement":
        return [("wait", 60, "still"),
                ("do", "Start moving / flexing / shaking the nappy with the Nicla attached. Press Enter as you start.",
                 "move_start", "moving", None),
                ("wait", 180, "moving"),
                ("do", "Stop moving, lay it flat and press Enter.", "move_end", "still_after", None),
                ("wait", 60, "still_after")]
    if a.protocol == "poo":
        return [("wait", a.baseline, "baseline"),
                ("do", "Place the poo proxy next to the gas sensor now, then press Enter.", "poo", "after_poo", "soiled"),
                ("wait", 300, "after_poo")]
    raise SystemExit(f"unknown protocol {a.protocol}")


class SimSource:
    """Simulated pod that reacts to bench events (for testing and example runs)."""

    def __init__(self, a, speed):
        from sensor import load_cal
        from simulate import SimPod
        self.pod = SimPod(load_cal(), capacity_ml=a.sim_capacity, absorb_tau=a.sim_tau, rewet=a.sim_rewet,
                          breath=a.sim_breath, climate="nappy", worn=False, seed=a.seed)
        self.pod.asleep = True
        self.speed, self.t0, self.last = speed, time.time(), time.time()

    def event(self, ev, a):
        if ev.startswith("insult"):
            self.pod.wee(a.volume)
        elif ev == "poo":
            self.pod.poo()
        elif ev == "press_start":
            self.pod.pressing = True
        elif ev == "press_end":
            self.pod.pressing = False
        elif ev == "move_start":
            self.pod.asleep = False
        elif ev == "move_end":
            self.pod.asleep = True

    def read(self):
        now = time.time()
        n = int((now - self.last) * self.speed)
        if n <= 0:
            return []
        self.last += n / self.speed
        rows = []
        for _ in range(n):
            r = self.pod.step()
            r["t"] = self.t0 + r["t"]
            rows.append(r)
        return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("protocol", choices=["insult", "movement", "poo"])
    ap.add_argument("--nappy", required=True, help='label for the product, e.g. "Brand A size 3" or "Prototype B"')
    ap.add_argument("--placement", default="under topsheet")
    ap.add_argument("--port", action="append", default=[])
    ap.add_argument("--volume", type=float, default=30.0)
    ap.add_argument("--insults", type=int, default=4)
    ap.add_argument("--wait", type=int, default=300, help="seconds after each insult")
    ap.add_argument("--baseline", type=int, default=120)
    ap.add_argument("--press", action="store_true", help="add a press test for rewet at the end")
    ap.add_argument("--prewet", action="store_true", help="movement run on an already-wet nappy")
    ap.add_argument("--out-dir", default="bench")
    # testing without hardware
    ap.add_argument("--simulate", action="store_true")
    ap.add_argument("--auto", action="store_true", help="fire events automatically (no Enter needed)")
    ap.add_argument("--speed", type=float, default=1.0, help="simulated seconds per real second")
    ap.add_argument("--sim-capacity", type=float, default=150.0)
    ap.add_argument("--sim-tau", type=float, default=90.0)
    ap.add_argument("--sim-rewet", type=float, default=0.25)
    ap.add_argument("--sim-breath", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    os.makedirs(a.out_dir, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", a.nappy.lower()).strip("-")
    run_id = f"{slug}_{a.protocol}_{time.strftime('%H%M%S')}"
    out = os.path.join(a.out_dir, run_id + ".csv")

    if a.simulate:
        src = SimSource(a, a.speed)
    else:
        from pod_stream import PodStream
        src = PodStream(a.port)

    keys = queue.Queue()
    if not a.auto:
        threading.Thread(target=lambda: [keys.put(line) for line in sys.stdin], daemon=True).start()

    steps = protocol_steps(a)
    truth = "wet" if (a.protocol == "movement" and a.prewet) else "dry"
    phase, pending_event = steps[0][2] if steps[0][0] == "wait" else "start", ""
    say = (lambda *m, **k: None) if a.quiet else print
    say(f"\nRecording {a.protocol} run for '{a.nappy}' ({a.placement}) -> {out}\n")

    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        sim_clock = [None]

        def pump():
            rows = src.read()
            for r in rows:
                global_t = r["t"]
                sim_clock[0] = global_t
                w.writerow([f"{global_t:.3f}", run_id, a.nappy, a.placement, a.protocol,
                            "simulated" if a.simulate else "bench", phase, truth, pump.event,
                            a.volume if pump.event.startswith("insult") else ""] + [r.get(c) for c in COLS])
                pump.event = ""
            f.flush()
            return len(rows)
        pump.event = ""

        def now():
            return sim_clock[0] if a.simulate and sim_clock[0] else time.time()

        # wait for data
        t_wait = time.time()
        while pump() == 0 and sim_clock[0] is None:
            if time.time() - t_wait > 15:
                raise SystemExit("no data from the boards - check the ports with pod_stream.py")
            time.sleep(0.2)

        for st in steps:
            if st[0] == "wait":
                _, secs, phase = st
                end = now() + secs
                last_print = 0
                while now() < end:
                    pump()
                    if not a.quiet and time.time() - last_print > 5:
                        last_print = time.time()
                        print(f"  [{phase}] {max(0, end - now()):5.0f} s left", end="\r")
                    time.sleep(0.05 if a.simulate else 0.3)
                say(" " * 40, end="\r")
            else:
                _, prompt, event, phase_after, truth_after = st
                say(f"\n>> {prompt}")
                if not a.auto:
                    while True:
                        pump()
                        try:
                            keys.get(timeout=0.3)
                            break
                        except queue.Empty:
                            pass
                if a.simulate:
                    src.event(event, a)
                pump.event = event
                phase = phase_after
                if truth_after:
                    truth = truth_after
                say(f"   marked '{event}'")
        pump()
    say(f"\nSaved {out}\nNext: python validate.py   and open http://localhost:8000/lab")
    return out


if __name__ == "__main__":
    main()

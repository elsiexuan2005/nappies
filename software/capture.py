"""Record labelled sensor data from BOTH boards (merged, 1 row per second).

  python capture.py                                    # auto-detects the UNO Q + Nicla
  python capture.py --port COM3 --port COM4            # or name the ports (any order)

While it runs, type a label and press Enter to switch what you're recording:
  dry       moisture sensor dry
  wet50     your 50 % water test
  wet100    sensor fully wet
  gas_base  gas sensor in clean air (let it warm up first!)
  gas_voc   vinegar / alcohol swab held near the gas sensor
  still     Nicla lying still on the table
  active    Nicla being moved / shaken gently
(The DHT11 room temperature/humidity is recorded alongside; it needs no calibration.)
  q         quit
About 60 s per label is plenty. Output goes to captures/capture_<time>.csv
"""
import argparse
import csv
import os
import sys
import threading
import time

from pod_stream import COLS, PodStream


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", action="append", default=[], help="default: auto-detect Arduino boards")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    os.makedirs("captures", exist_ok=True)
    out = a.out or f"captures/capture_{time.strftime('%H%M%S')}.csv"

    st = {"label": "unlabeled", "quit": False}

    def keyboard():
        print(__doc__.split("While it runs,")[1].split("Output")[0])
        for line in sys.stdin:
            s = line.strip()
            if s == "q":
                st["quit"] = True
                break
            if s:
                st["label"] = s
                print(f"\n>> now recording label: {s}")

    threading.Thread(target=keyboard, daemon=True).start()
    ps = PodStream(a.port)

    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["host_time", "label"] + COLS)
        n = 0
        while not st["quit"]:
            time.sleep(0.5)
            for r in ps.read():
                w.writerow([f"{r['t']:.3f}", st["label"]] + [r[c] for c in COLS])
                n += 1
                if n % 5 == 0:
                    s = ps.status()
                    print(f"[{st['label']:>9}] moisture={r['moisture']:7.1f} gas={r['gas']:7.1f} "
                          f"acc_std={r['acc_std']:.4f} gyro={r['gyro_mean']:6.1f}  "
                          f"sensors={'OK' if s['sensors'] else '--'} imu={'OK' if s['imu'] else '--'} ({n} rows)")
            f.flush()
            for e in ps.errors:
                print("ERROR", e)
            ps.errors.clear()
    print(f"saved {out}")


if __name__ == "__main__":
    main()

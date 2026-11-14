"""Create SIMULATED example bench runs in bench/examples/ so the Product Lab page
has something to show before you record real runs. They are clearly marked as
simulated everywhere, and are hidden as soon as real runs exist in bench/.

  python make_examples.py
"""
import glob
import os
import sys

import bench

EXAMPLES = [
    # nappy label, protocol, product parameters
    ("Example A (simulated)", "insult", ["--sim-tau", "55", "--sim-capacity", "190", "--sim-rewet", "0.12", "--sim-breath", "1.4", "--press"]),
    ("Example B (simulated)", "insult", ["--sim-tau", "140", "--sim-capacity", "135", "--sim-rewet", "0.30", "--sim-breath", "0.7", "--press"]),
    ("Example A (simulated)", "movement", []),
    ("Example A (simulated)", "poo", []),
]


def main():
    os.makedirs("bench/examples", exist_ok=True)
    for f in glob.glob("bench/examples/*.csv"):
        os.remove(f)
    for i, (nappy, proto, extra) in enumerate(EXAMPLES):
        sys.argv = ["bench.py", proto, "--nappy", nappy, "--simulate", "--auto", "--speed", "3000",
                    "--quiet", "--out-dir", "bench/examples", "--seed", str(i + 1)] + extra
        out = bench.main()
        print("wrote", out)


if __name__ == "__main__":
    main()

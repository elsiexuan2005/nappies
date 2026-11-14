
import csv
import json
import time
from datetime import datetime
from pathlib import Path

import serial
from serial.tools import list_ports

BAUD_RATE = 115200

# Change label for each experiment.
EXPERIMENT = ""

PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

filename = (
    datetime.now().strftime("%Y%m%d_%H%M%S")
    + f"_{EXPERIMENT}.csv"
)

output_file = DATA_DIR / filename

# Automatically find UNO Q using Arduino's USB vendor ID.
ports = [
    port.device
    for port in list_ports.comports()
    if port.vid == 0x2341
]

if not ports:
    raise RuntimeError(
        "Arduino UNO Q not found. Check USB connection."
    )

if len(ports) > 1:
    raise RuntimeError(
        f"Multiple Arduino ports found: {ports}"
    )

port = ports[0]

print(f"Connecting to {port}")
print(f"Experiment: {EXPERIMENT}")
print(f"Saving to: {output_file}")

with serial.Serial(port, BAUD_RATE, timeout=2) as arduino:
    with open(output_file, "w", newline="") as file:
        writer = csv.writer(file)

        writer.writerow([
            "recorded_at",
            "timestamp_ms",
            "sensor_raw",
            "experiment"
        ])

        print("Recording... Press Ctrl+C to stop.")

        try:
            while True:
                line = arduino.readline().decode(
                    "utf-8", errors="replace"
                ).strip()

                if not line:
                    continue

                try:
                    reading = json.loads(line)

                    timestamp = int(reading["timestamp_ms"])
                    sensor = int(reading["sensor_raw"])

                except (
                    json.JSONDecodeError,
                    KeyError,
                    ValueError,
                    TypeError
                ):
                    continue

                writer.writerow([
                    datetime.now().isoformat(
                        timespec="milliseconds"
                    ),
                    timestamp,
                    sensor,
                    EXPERIMENT
                ])

                # Ensure data is written during recording
                file.flush()

                print(
                    f"{timestamp} ms | Sensor: {sensor}"
                )

        except KeyboardInterrupt:
            print("\nRecording stopped.")

print(f"Data saved to: {output_file}")

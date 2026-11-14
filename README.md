# Nappies: PodCare (DurHack, P&G Challenge 2: wearable sensors)

A sensing pod for nappies: wetness, poo (gas), skin climate (humidity/temperature) and movement. It turns the
signals into one clear action for caregivers ("change now" / "can wait") and into product-performance insight
for P&G R&D (Product Lab) and business (fleet dashboard). Bench tests and simulated data only: no people,
no personal data.

| Folder | What's inside |
|---|---|
| `hardware/` | Arduino sketches. `moisture_sensor/`, `gas_sensor/`, `record_sensor.py`: the original single-sensor sketches and recorder. `pod_sensors/`: UNO Q sketch reading moisture + gas + DHT11 together. `nicla_vision/`: camera motion detection (OpenMV); `motion_stream.py` streams it once per second for the software. `nicla_imu/`: Arduino-firmware alternative using the IMU. `../data/`: recordings |
| `software/` | Python: models, dashboards (parent app, business, Product Lab), bench protocol + validation, edge/cloud microservices, Docker/Kubernetes deployment |

Start here: [`software/README.md`](software/README.md). For the architecture and edge/cloud split, see
[`software/ARCHITECTURE.md`](software/ARCHITECTURE.md); for the pitch story, see [`software/DESIGN.md`](software/DESIGN.md).

Quick start (from `software/`):
```bash
pip install -r requirements-dev.txt
python train.py --sessions 250 && python make_examples.py
python server.py                 # http://localhost:8000  (/business, /lab)   add --mode serial for the real boards
python calibrate.py <dry/wet50/wet100 recordings>   # before trusting live readings
```

> ⚠️ **Arduino UNO Q:** A0 and A1 are 3.3 V only (not 5 V tolerant). Make sure the gas sensor output on A1
> can't exceed 3.3 V (use a voltage divider if it's powered from 5 V).

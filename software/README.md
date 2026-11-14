# Smart Diaper Pod: DurHack P&G challenge

> **Repo layout.** `../hardware/` holds the Arduino sketches: the team's original single-sensor sketches and
> `record_sensor.py`, plus `pod_sensors/` (UNO Q: moisture + gas + DHT11 in one sketch) and `nicla_imu/`.
> This `software/` folder is the Python side: models, dashboards, bench tools and the microservices.
> Run every command below from inside `software/`.
>
> **Three ways to run it**
> 1. `python server.py`: one process, all three dashboards. **Safest for the live demo.**
> 2. `python run_local.py`: the full microservice stack on one laptop, no Docker (see `ARCHITECTURE.md`).
> 3. Docker: `deploy/docker-compose.edge.yml` (hub) + `deploy/docker-compose.cloud.yml` (cloud), or `deploy/k8s/`.
>
> **Motion from the Nicla Vision: two options.**
> - **Camera (team's approach, OpenMV firmware):** run `../hardware/nicla_vision/motion_detection.py` as it is, or
>   `motion_stream.py` (same detection, one JSON line per second, plus the Nicla's IMU if available). Save it as
>   `main.py` on the Nicla's drive and close the OpenMV IDE so the software can open the port. Images never leave the Nicla.
> - **IMU (Arduino firmware):** flash `../hardware/nicla_imu/nicla_imu.ino`.
>
> Either way the software converts it into the same still/moving/asleep signals.
>
> **Calibrate before trusting live readings.** The team's recordings suggest this moisture sensor reads **higher when
> wetter**, the opposite of the default values. Until `calibration.json` exists, the app says "not calibrated".
> Record about 30 s each of dry, 50% water and 100% water (`python capture.py`, or `record_sensor.py` with
> EXPERIMENT set to dry / wet50 / wet100), then `python calibrate.py <files>`. Either direction works.
>
> ⚠️ **UNO Q wiring:** A0 and A1 are 3.3 V only (not 5 V tolerant). Check the gas sensor output never exceeds
> 3.3 V; use a voltage divider if it can. Details in "Hardware notes".

Setup: **Arduino + breadboard** (DFRobot capacitive moisture v2 on A0, gas sensor on A1, DHT11 temperature/humidity on D2) and **Nicla Vision** (IMU), each on its own USB port. The laptop merges both into one stream every second → ML → live dashboard:
**dry / wet / soiled**, **chance the diaper is full within the next hour**, **asleep / moving**, and one clear instruction for the parent.

Approach: **synthetic data from published infant figures, calibrated to our real sensors.** No hours of experiments needed.

## Build plan (24 h)

| # | Step | Time | Who |
|---|------|------|-----|
| 1 | Flash `../hardware/pod_sensors` to the UNO Q and `../hardware/nicla_imu` to the Nicla; check with `pod_stream.py` | 30 min | hardware |
| 2 | `capture.py`: about 60 s each of `dry, wet50, wet100, gas_base, gas_voc, still, active` | 20 min | hardware |
| 3 | `calibrate.py` → `calibration.json` | 2 min | anyone |
| 4 | Look up and cite the physiology numbers in `config.py` (marked ASSUMPTION) | 45 min | research/pitch |
| 5 | `train.py` → `models.joblib` + `models_report.txt` | 2 min | ML |
| 6 | `streamlit run live_app.py`: demo with the real board (simulator as backup) | 1–2 h polish | app |
| 7 | Parent app + business dashboard: `python server.py` | done | — |
| 8 | Pitch deck + rehearse demo | 3 h | all |

Steps 4 and 7 can run in parallel with 1–3.

## Run order

```bash
pip install -r requirements-dev.txt     # requirements.txt alone is enough for the Docker images

# 0. check both boards stream (boards are auto-detected by Arduino's USB ID, like record_sensor.py)
python pod_stream.py                    # or: --port COM3 --port COM4 ;  --list shows all ports
# 1. record calibration data
python capture.py
#    (recordings from ../hardware/record_sensor.py work too: python calibrate.py ../data/*.csv)
# 2. build calibration.json
python calibrate.py captures/*.csv
# 3. train (about 1 min), then make the example bench runs for the Lab page
python train.py --sessions 250
python make_examples.py
# 4. live demo
streamlit run live_app.py
```

Without hardware, steps 3 and 4 work straight away using placeholder calibration (DFRobot SEN0193 wiki values scaled to the UNO Q's 12-bit ADC). For Replay mode, first run `python simulate.py` to create `synthetic_sample.csv`.
For the live board: `python server.py --mode serial` (ports auto-detected). The app shows whether each board is connected.

## Bench tests, validation and Product Lab (P&G brief: bench only, no people)

P&G provides nappies and lets us apply test fluids. Every run follows a timed protocol, and you press Enter at
the moment you pour fluid, so each row is labelled with the **true** state. That gives the labelled bench
dataset the brief asks for.

```bash
python bench.py insult   --nappy "Brand A size 3" --placement "under topsheet" --press
python bench.py movement --nappy "Brand A size 3"      # no fluid: false-alarm test
python bench.py poo      --nappy "Brand A size 3"
python upload_bench.py bench/*.csv     # microservices: send runs to the cloud Product Lab
python validate.py                     # accuracy, detection delay, false alarms, plots in bench/plots/
python train.py --bench "bench/*.csv"  # optional: also learn from your real bench runs
```
- **Insult protocol:** 2 min dry → 4 × 30 mL saline, 5 min apart → optional press test (rewet). About 25 min per product.
- **Try two placements:** under the topsheet vs outside the backsheet (`--placement`). Outside = no contact with fluid or skin.
- **Product Lab** (`/lab`) compares products: time to soak away surface fluid after each insult, rise time,
  saturation added per insult, insults until full, rewet under pressure, humidity inside the nappy, plus the
  validation figures. `python make_examples.py` creates **simulated** example runs so the page works before you have
  real data. They're labelled "simulated" and are hidden automatically once real runs exist in `bench/`.

## Dashboards (parent app + P&G business view)

```bash
python train.py --sessions 250                      # once, creates models.joblib
python server.py                                    # simulator, no hardware needed
python server.py --mode serial                      # live pod (ports auto-detected)
```
Open **http://localhost:8000/** (parent app), **/business** (P&G business) and **/lab** (P&G R&D Product Lab).
`--dht nappy` (default) treats the DHT11 as inside the nappy (skin climate); `--dht room` treats it as a room sensor near the cot.
Everything is served locally, including fonts and charts, so it works without wifi.

**Parent app (phone UI):** a plain-English answer to "do I need to change?", diaper fill drop, time in this diaper,
chance it's full within an hour, and today's wellness panel:
- *Healthy diaper time*: share of today in a clean diaper that isn't over-full
- *Hydration*: wet diapers so far vs expected for age
- *Gut*: poo count and regularity (colour check needs the camera, coming later)
- *Skin*: rash risk from minutes in a soiled or over-full diaper
- *Sleep and activity* (Nicla IMU): a strip of the day showing asleep / calm / active with wees, poos and changes marked;
  total sleep, calm and active time; longest sleep stretch; number of sleeps; and **"woken by the diaper"**:
  how many wake-ups came within 10 min of a wee or poo. Demo it: baby asleep → Add a wee → Baby awake.
- *Skin climate* (DHT11 inside the nappy, default): humidity and temperature at the waistband. Long spells above 85% RH
  raise the rash-risk score and show an irritation note (thresholds are assumptions to tune on the bench).
- *Room* (with `--dht room`): temperature and humidity against the Lullaby Trust safe-sleep range of 16–20°C, the share of
  today's sleep spent in that range, and a warning under the main message if the room is too warm or cold while the baby sleeps.
  Demo it: set Room temperature to 26°C. The DHT11 is accurate to about ±2°C and measures the air, **not** the baby's body temperature.
- Timeline of wees, poos, changes and sleep, plus the current diaper's fill chart
It switches to a dark night theme while the baby sleeps. The demo panel on the right adds wees and poos, toggles sleep, and speeds up time.
Events before the session starts are simulated so the day looks full (`--no-demo-history` turns this off).

**Business dashboard (P&G):** a simulated fleet of 3,000 pods, each baby run with and without the pod:
- Leak rate vs how close the baby is to the size limit → size-up nudges
- Overnight fill by size → R&D and absorbency claims
- 4-week diaper demand by size → auto-replenishment and supply planning
- With vs without pod: leaks, days in a too-small size, minutes in a soiled diaper
- Retention calculator with sliders → subscription revenue
Assumptions are listed on the page and in `fleet.py`.

**Deliberately not included:** urine pH/acidity (none of our sensors can measure it; a next version could read a
colour-changing pH strip with the Nicla camera) and supplement suggestions (that's medical advice; the app suggests
everyday actions like "offer a feed" and points to a doctor instead).

## Files

| File | What it does |
|---|---|
| `../hardware/pod_sensors/` | UNO Q: A0/A1 at 50 Hz + DHT11 every 2 s → `{"timestamp_ms","sensor_raw","gas_raw","temp_c","humidity"}` once per second |
| `../hardware/nicla_imu/` | Nicla Vision (Arduino firmware): IMU at 50 Hz → `{"timestamp_ms","acc_mean","acc_std","gyro_mean"}` once per second |
| `../hardware/nicla_vision/` | Nicla Vision (OpenMV): team's `motion_detection.py` camera motion (read as-is) and `motion_stream.py` (JSON once per second, camera + IMU) |
| `../hardware/moisture_sensor/`, `gas_sensor/`, `record_sensor.py` | Team's original single-sensor sketches and recorder. Still work with everything here (`pod_stream.py` reads their JSON, `calibrate.py` reads their CSVs) |
| `services/edge/*`, `services/cloud/*` | Microservices (see `ARCHITECTURE.md`) |
| `run_local.py`, `deploy/` | Run the services locally / Docker Compose / Kubernetes |
| `pod_stream.py` | Reads both USB ports at once, merges into one row per second, shows connection status |
| `capture.py` | Records the merged stream with labels you type live |
| `calibrate.py` | Turns labelled captures into `calibration.json` |
| `config.py` | Physiology and diaper assumptions (verify and cite these) |
| `sensor.py` | Raw ADC → saturation 0–1, gas → normalised rise |
| `simulate.py` | Synthetic day/overnight diaper sessions with realistic confounders, plus `SimPod` for the live demo |
| `features.py` | Same feature code for training and live use |
| `train.py` | Trains the 4 models, prints metrics |
| `recommend.py` | Model outputs → "CHANGE NOW / CHANGE SOON / CAN WAIT / ALL GOOD" |
| `live_app.py` | Streamlit debug dashboard: Simulator / Serial / Replay CSV |
| `bench.py` | Guided, labelled bench protocols (insult / movement / poo); `--simulate` to test without hardware |
| `validate.py`, `benchlib.py` | Model check against labelled bench runs, Product Lab metrics and findings |
| `make_examples.py` | Simulated example bench runs for the Lab page (clearly marked) |
| `DESIGN.md` | Pitch story: disposable strip + reusable pod, placement, edge vs cloud, trust, privacy, limits |
| `engine.py` | Runs the models live, detects wees/poos/changes/sleep, computes today's wellness metrics |
| `fleet.py` | Fleet simulation → P&G business insights |
| `server.py` | Serves both dashboards + JSON API (`/api/state`, `/api/business`, `/api/action`) |
| `web/parent.html`, `web/business.html` | The two dashboards (fonts and Chart.js stored in `web/vendor/`) |

## Models

| Model | Type | Inputs | Result on held-out synthetic sessions |
|---|---|---|---|
| Diaper state (dry/wet/soiled) | Random forest | saturation, peak saturation, gas rise, slopes, time | ~97–99 % accuracy (varies by run) |
| Activity (still/moving) | Decision tree, depth 2 | accel std, gyro | ~99 % (tiny tree, so it can run on the Nicla) |
| Full within 60 min | Gradient boosting classifier | saturation, peak, stillness, size | AUC ~0.90 before the diaper is full |
| Rough time to full | Gradient boosting regressor | + elapsed time, fill rate | display only, ±~60 min |

**Be upfront about these numbers in the pitch:** they are measured on synthetic data, so real-world accuracy will be lower. The synthetic data includes confounders on purpose (room smells, small poos, the sensor losing contact when the baby moves, differences between units), so the models aren't trivially perfect. The time-to-full estimate is uncertain mostly because *when the next wee happens* is random. That's why the main signal is a probability ("62 % chance it's full within the hour"), not an exact minute.

## Demo script (simulator or real board)

1. Dry → **ALL GOOD**
2. Wee (pour water on the sensor) → **WET**; asleep → **CAN WAIT**
3. More wees → probability rises → **CHANGE SOON** → **CHANGE NOW**
4. Poo (vinegar/alcohol swab near the gas sensor) → **SOILED → CHANGE NOW**
5. Press *Diaper changed* → resets the baseline
Use speed-up ×60–×600 in the simulator to show a whole night in a minute.

## Hardware notes

- **Arduino UNO Q (the team's board):** the MCU pins are 3.3 V, and **A0 and A1 are not 5 V tolerant**. Moisture on A0, gas on A1, 12-bit readings (0–4095).
  - **Gas sensor:** if powered from 5 V, measure its output at full response. If it can exceed 3.3 V, add a divider (e.g. 10k top / 20k bottom) before A1.
  - **DHT11:** power it from 3.3 V.
- **Moisture (DFRobot SEN0193, capacitive v2):** outputs 0–3.0 V (safe on A0) and reads **lower** when wet. Roughly dry ≈ 3150, in water ≈ 1580 on the UNO Q (dry ≈ 520 / water ≈ 260 on a classic 5 V Uno).
- **One sketch per board:** the team's `moisture_sensor.ino` and `gas_sensor.ino` each read one sensor. Flash `pod_sensors.ino` to get both (plus the DHT11) from the same board.
- **DHT11:** module VCC → 5 V, GND → GND, DATA → D2 (a bare 4-pin sensor needs a 10k pull-up from DATA to 5 V).
  Install "DHT sensor library" by Adafruit (plus "Adafruit Unified Sensor"). Set `USE_DHT 0` in the sketch if it isn't connected.
  Put it near the cot, away from your laptop and the gas sensor's heater, which are warm and will push the reading up.
- **Gas:** MQ-type heaters need a few minutes of warm-up before you calibrate. If the gas board has 4 pins (VCC/GND/SCL/SDA), it's I2C, not analog, and the Arduino sketch needs changing.
- **Nicla Vision:** in the Arduino IDE, select the "Arduino Nicla Vision" board (Mbed OS Nicla core) and install the `Arduino_LSM6DSOX` library.
- If the Nicla isn't plugged in, everything still runs; movement is treated as "still".
- Opening the serial port can reset the board, so the first ~2 s may have no data. That's normal.

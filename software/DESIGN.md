# PodCare: design story (for the pitch)

## The problem
- **Caregivers** can't see inside a nappy. They check too often (waking the baby) or too late (leaks, a sore bottom).
- **P&G R&D** mostly learns about product performance by inspecting nappies *after* use. What happens *during* use isn't measured: how fast fluid is absorbed, how wet the skin side stays, how humid it gets, and how the nappy behaves when the baby moves.

## What we built
A clip-on pod plus a cheap sensing element in the nappy. The pod turns raw signals into:
- for caregivers: "change now / can wait", skin climate, sleep and activity
- for P&G R&D: insult-by-insult product performance (Product Lab)
- for P&G business: fleet insights such as size-up timing, demand and leak reduction

## Product architecture: disposable element + reusable electronics
| Part | Product version | Bench prototype stand-in |
|---|---|---|
| Wetness | **Printed capacitive electrodes** on the backsheet (disposable, a few pence target). Sensing through the backsheet means no contact with skin or fluid. | DFRobot SEN0193 capacitive probe |
| Skin climate | Tiny digital humidity/temperature sensor in the pod, facing a breathable window at the waistband | DHT11 inside the nappy at the waistband |
| Poo / ammonia | Low-power MEMS gas sensor in the pod | MQ-type analog gas sensor |
| Movement / sleep | IMU in the pod | Nicla Vision IMU |
| Brain + link | Low-power MCU with Bluetooth LE, coin cell or rechargeable | Arduino + Nicla over USB to a laptop |

**Why this split:** the only thing thrown away is a printed strip, with no battery or chip in the nappy. The pod is reusable and never sits in the wet zone, which deals with fluid exposure, cost and waste. Unlike the discontinued Pampers Lumi (wetness only, special diapers), this detects poo and skin climate as well.

## Sensor placement (and what to test on the bench)
- **Moisture:** centre of the wet zone. Bench comparison: *under the topsheet* vs *outside the backsheet*. If the outside placement works, sensing is truly "invisible".
- **Humidity/temperature:** waistband at the back, out of the direct fluid path (the DHT11 isn't waterproof).
- **Gas and IMU:** in the pod at the front waistband.

## From signal to insight (the P&G data loop)
| Loop step | Our implementation |
|---|---|
| 1 Sense | Moisture, gas, humidity/temperature, IMU, sampled at 50 Hz and averaged to 1 Hz |
| 2 Collect | Serial today (BLE in the product). `pod_stream.py` merges both boards and survives one dropping out |
| 3 Store | Per-diaper buffer and day history on the device/phone. Raw data never needs to leave |
| 4 Analyse | Calibration to sensor-independent units; rolling features (level, slope, peak, gas rise, stillness) |
| 5 Interpret | Models: dry/wet/soiled, chance full within 1 h, still/moving. **Checked against labelled bench runs** |
| 6 Front end | Parent app (one sentence: change now / can wait), Product Lab and business dashboard for P&G |
| 7 Insight | Healthy diaper time, skin climate, woken by the diaper; absorption speed, rewet, capacity |
| 8 Behaviour change | Change at the right time, size up before leaks, night diapers when wake-ups follow wees |

## Edge vs cloud, power, connectivity
- **On the device:** the still/moving model is a depth-2 decision tree, small enough to run on the pod. Diaper-state inference is light too.
- **Send events, not streams:** "wet", "poo", "nearly full" plus 1-minute summaries over BLE. That saves battery and keeps raw data private.
- **Store and forward:** the pod buffers events while the phone is out of range and syncs when it reconnects.
- **Latency:** wet is confirmed within seconds and poo within about 40 s on the bench (see `validate.py`). For a nappy, under a minute is fine.

## Trust: avoiding false alarms and alert fatigue
- Alerts come from **probabilities with sustained-detection rules** (at least 5 s), not single threshold crossings.
- "**Can wait**" when the baby is asleep and the nappy has headroom, so not every wee triggers an alert.
- Training data includes **room smells, small poos, the sensor losing contact during movement and differences between units**.
- **Movement-only bench runs** measure false alarms directly.

## Privacy and security (designed as if real data were involved)
- The hackathon uses **bench tests and simulated data only**; no people, no personal or health data.
- Product design: pseudonymous device IDs, encrypted BLE pairing, wellness insights processed on the phone, **opt-in** sharing of anonymised, aggregated usage with P&G, and clear retention limits.

## Honest limitations and next steps
- The models are trained on synthetic data and checked on bench runs. More real bench data improves them (`train.py --bench`).
- Once the moisture sensor saturates, it can't count further wees.
- DHT11: ±2 °C / ±5 % RH. MQ gas sensors drift with temperature and humidity; the DHT11 readings could correct that.
- **Not yet measured:** fit and pressure (next: thin force sensors at the leg cuffs and waist), and **skin pH** (next: a colour-changing pH patch read by the Nicla camera at change time).
- All business numbers are simulated projections with the assumptions listed on the page.

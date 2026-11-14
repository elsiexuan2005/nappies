// PodCare sensor board - Arduino UNO Q (moisture + gas + optional DHT11)
// Combines moisture_sensor.ino and gas_sensor.ino into ONE sketch, in the same JSON style,
// so both sensors (and the DHT11) stream together from one board.
//
// One JSON line per second (values averaged over the second, sampled at 50 Hz):
//   {"timestamp_ms":12345,"sensor_raw":3050.2,"gas_raw":612.4,"temp_c":21.0,"humidity":48.0}
// "sensor_raw" = moisture (same key as moisture_sensor.ino), "gas_raw" = gas (same as gas_sensor.ino).
// temp_c/humidity are null until the DHT11 gives its first good reading.
//
// !!! UNO Q WIRING WARNING !!!
// The UNO Q's MCU pins are 3.3 V, and A0 and A1 are NOT 5 V tolerant.
//  - DFRobot SEN0193 moisture (A0): output 0-3.0 V -> OK.
//  - Gas sensor (A1): if the module is powered from 5 V its output can exceed 3.3 V and damage A1.
//    Measure the output with a multimeter at full gas response; if it can go above 3.3 V, add a
//    voltage divider (e.g. 10k top / 20k bottom -> x0.67) or power the module from 3.3 V if it allows.
//  - DHT11 (D2): power from 3.3 V.
//
// Libraries: "DHT sensor library" by Adafruit (+ "Adafruit Unified Sensor"). The DHT11 uses
// tight bit timing; if it doesn't read on the UNO Q core, set USE_DHT 0 - everything else still works.
//
// Alert LED (optional): lights when fill is in the red zone (sat >= ~0.85) or when the laptop
// sends {"led":true}. Default pin is D3 so it does not clash with DHT11 on D2. If your LED is
// on D2, set USE_DHT 0 and LED_PIN 2.

#define USE_DHT 1
#define USE_LED 1
const int MOISTURE_PIN = A0;
const int GAS_PIN = A1;
const int DHT_PIN = 2;
const int LED_PIN = 3;                 // use 2 only if USE_DHT is 0

// Raw moisture at "red" fill (~85% sat). Team cal: dry 150, wet100 1600 → ~1382.
const int RED_MOISTURE = 1380;
const int DRY_MOISTURE = 150;          // for polarity (wet higher vs lower)

#if USE_DHT
#include <DHT.h>
DHT dht(DHT_PIN, DHT11);
#endif

const unsigned long SAMPLE_MS = 20;    // 50 Hz sampling
const unsigned long REPORT_MS = 1000;  // 1 line per second
const unsigned long DHT_MS = 2000;     // DHT11 needs >= 1-2 s between reads

unsigned long mSum = 0, gSum = 0, n = 0;
float tempC = NAN, hum = NAN;
bool softwareLed = false;

void setup() {
  Serial.begin(115200);
#if !defined(ARDUINO_ARCH_AVR)
  analogReadResolution(12);            // UNO Q: 0-4095 (classic AVR Uno stays 0-1023)
#endif
#if USE_DHT
  dht.begin();
#endif
#if USE_LED
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);
#endif
}

void printNum(float v, int digits) {
  if (isnan(v)) Serial.print("null"); else Serial.print(v, digits);
}

void pollLedCommand() {
  while (Serial.available() > 0) {
    String line = Serial.readStringUntil('\n');
    line.trim();
    if (line.indexOf("\"led\":true") >= 0 || line.indexOf("\"led\": true") >= 0) {
      softwareLed = true;
    } else if (line.indexOf("\"led\":false") >= 0 || line.indexOf("\"led\": false") >= 0) {
      softwareLed = false;
    }
  }
}

void loop() {
  static unsigned long lastSample = 0, lastReport = 0, lastDht = 0;
  unsigned long now = millis();

  pollLedCommand();

  if (now - lastSample >= SAMPLE_MS) {
    lastSample = now;
    mSum += analogRead(MOISTURE_PIN);
    gSum += analogRead(GAS_PIN);
    n++;
  }

#if USE_DHT
  if (now - lastDht >= DHT_MS) {
    lastDht = now;
    float t = dht.readTemperature();
    float h = dht.readHumidity();
    if (!isnan(t) && !isnan(h)) { tempC = t; hum = h; }    // keep last good reading
  }
#endif

  if (now - lastReport >= REPORT_MS && n > 0) {
    lastReport = now;
    float moisture = (float)mSum / n;
    Serial.print("{\"timestamp_ms\":");
    Serial.print(now);
    Serial.print(",\"sensor_raw\":");
    Serial.print(moisture, 1);
    Serial.print(",\"gas_raw\":");
    Serial.print((float)gSum / n, 1);
    Serial.print(",\"temp_c\":");
    printNum(tempC, 1);
    Serial.print(",\"humidity\":");
    printNum(hum, 1);
    Serial.println("}");
#if USE_LED
    bool wetHigh = (RED_MOISTURE > DRY_MOISTURE) ? (moisture >= RED_MOISTURE)
                                                 : (moisture <= RED_MOISTURE);
    digitalWrite(LED_PIN, (wetHigh || softwareLed) ? HIGH : LOW);
#endif
    mSum = gSum = n = 0;
  }
}

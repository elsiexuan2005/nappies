// Outer-nappy leak probe + alert LED (separate board from pod_sensors).
//
// Streams once every 500 ms:
//   {"timestamp_ms":..,"sensor_raw":..,"leak_detected":true|false}
//
// LED on D2 lights when:
//   - the outer probe confirms a leak (local), OR
//   - the laptop says the parent app is in a red alert state:
//       {"led":true} / {"led":false}   (written by software/pod_stream.py)
//
// Calibration: match your probe (team defaults: dry ~150, wet ~1600 on 12-bit UNO Q).
// Do not flash this on the same board as pod_sensors (D2 is DHT11 there).

const int MOISTURE_PIN = A0;
const int LED_PIN = 2;

// CALIBRATION — replace after measuring dry vs water-outside-nappy
const int DRY_READING = 150;
const int LEAK_READING = 1600;

const int LEAK_THRESHOLD =
    (DRY_READING + LEAK_READING) / 2;

const int CONFIRM_SAMPLES = 3;

int wetCount = 0;
int dryCount = 0;

bool leakDetected = false;
bool softwareLed = false;   // forced on by the dashboard when the fill alert is red

void setup() {
    Serial.begin(115200);
#if !defined(ARDUINO_ARCH_AVR)
    analogReadResolution(12);
#endif
    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, LOW);
    Serial.println("Leak detection started");
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
    pollLedCommand();

    int moisture = analogRead(MOISTURE_PIN);

    bool waterOutsideNappy;
    if (LEAK_READING > DRY_READING) {
        waterOutsideNappy = moisture > LEAK_THRESHOLD;
    } else {
        waterOutsideNappy = moisture < LEAK_THRESHOLD;
    }

    if (waterOutsideNappy) {
        wetCount++;
        dryCount = 0;
    } else {
        dryCount++;
        wetCount = 0;
    }

    if (wetCount >= CONFIRM_SAMPLES) {
        leakDetected = true;
    }
    if (dryCount >= CONFIRM_SAMPLES) {
        leakDetected = false;
    }

    // Red alert from the app OR confirmed outer leak
    digitalWrite(LED_PIN, (leakDetected || softwareLed) ? HIGH : LOW);

    Serial.print("{\"timestamp_ms\":");
    Serial.print(millis());
    Serial.print(",\"sensor_raw\":");
    Serial.print(moisture);
    Serial.print(",\"leak_detected\":");
    Serial.print(leakDetected ? "true" : "false");
    Serial.println("}");

    delay(500);
}

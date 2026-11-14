
const int MOISTURE_PIN = A0;
const int LED_PIN = 2;

// CALIBRATION
// Replace these after measuring the sensor
// outside the nappy in dry and leaked-water conditions.
const int DRY_READING = 350;
const int LEAK_READING = 1000;

const int LEAK_THRESHOLD =
    (DRY_READING + LEAK_READING) / 2;

// Require 3 consecutive readings
const int CONFIRM_SAMPLES = 3;

int wetCount = 0;
int dryCount = 0;

bool leakDetected = false;

void setup() {
    Serial.begin(115200);

    analogReadResolution(12);

    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, LOW);

    Serial.println("Leak detection started");
}

void loop() {
    int moisture = analogRead(MOISTURE_PIN);

    // Determine direction from calibration
    bool waterOutsideNappy;

    if (LEAK_READING > DRY_READING) {
        waterOutsideNappy =
            moisture > LEAK_THRESHOLD;
    } else {
        waterOutsideNappy =
            moisture < LEAK_THRESHOLD;
    }

    if (waterOutsideNappy) {
        wetCount++;
        dryCount = 0;
    } else {
        dryCount++;
        wetCount = 0;
    }

    // Confirm leakage
    if (wetCount >= CONFIRM_SAMPLES) {
        leakDetected = true;
    }

    // Clear alert after 3 dry readings
    if (dryCount >= CONFIRM_SAMPLES) {
        leakDetected = false;
    }

    // Control LED
    digitalWrite(
        LED_PIN,
        leakDetected ? HIGH : LOW
    );

    // Send data to software/dashboard
    Serial.print("{\"timestamp_ms\":");
    Serial.print(millis());

    Serial.print(",\"sensor_raw\":");
    Serial.print(moisture);

    Serial.print(",\"leak_detected\":");
    Serial.print(leakDetected ? "true" : "false");

    Serial.println("}");

    delay(500);
}

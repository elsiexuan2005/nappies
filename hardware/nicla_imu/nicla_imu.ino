// PodCare motion sensor - Arduino Nicla Vision (IMU only), plugged into the laptop/hub by USB.
// One JSON line per second, same style as the team's other sketches:
//   {"timestamp_ms":12345,"acc_mean":1.0012,"acc_std":0.0041,"gyro_mean":0.62}
// acc_mean / acc_std : magnitude of acceleration (g) over the last second (sampled at 50 Hz)
// gyro_mean          : magnitude of angular rate (deg/s) over the last second
// Board: "Arduino Nicla Vision" (Arduino Mbed OS Nicla Boards core)
// Library: Arduino_LSM6DSOX (Library Manager)

#include <Arduino_LSM6DSOX.h>

const uint32_t SAMPLE_MS = 20;
const uint32_t REPORT_MS = 1000;

double accSum = 0, accSq = 0, gyroSum = 0;
uint32_t n = 0;

void setup() {
  Serial.begin(115200);
  while (!Serial && millis() < 3000) {}
  if (!IMU.begin()) {
    Serial.println("# IMU init failed");
    while (true) delay(1000);
  }
}

void loop() {
  static uint32_t lastSample = 0, lastReport = 0;
  uint32_t now = millis();

  if (now - lastSample >= SAMPLE_MS) {
    lastSample = now;
    float ax, ay, az, gx, gy, gz;
    if (IMU.accelerationAvailable() && IMU.gyroscopeAvailable()) {
      IMU.readAcceleration(ax, ay, az);
      IMU.readGyroscope(gx, gy, gz);
      float a = sqrt(ax * ax + ay * ay + az * az);
      float g = sqrt(gx * gx + gy * gy + gz * gz);
      accSum += a; accSq += (double)a * a; gyroSum += g; n++;
    }
  }

  if (now - lastReport >= REPORT_MS && n > 0) {
    lastReport = now;
    double mean = accSum / n;
    double var = accSq / n - mean * mean;
    if (var < 0) var = 0;
    Serial.print("{\"timestamp_ms\":");
    Serial.print(now);
    Serial.print(",\"acc_mean\":");
    Serial.print(mean, 4);
    Serial.print(",\"acc_std\":");
    Serial.print(sqrt(var), 4);
    Serial.print(",\"gyro_mean\":");
    Serial.print(gyroSum / n, 2);
    Serial.println("}");
    accSum = accSq = gyroSum = 0; n = 0;
  }
}

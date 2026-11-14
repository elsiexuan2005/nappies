
const int MOISTURE_PIN = A0;

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);
}

void loop() {
  int moisture = analogRead(MOISTURE_PIN);

  Serial.print("{\"timestamp_ms\":");
  Serial.print(millis());

  Serial.print(",\"sensor_raw\":");
  Serial.print(moisture);

  Serial.println("}");

  delay(500);
}

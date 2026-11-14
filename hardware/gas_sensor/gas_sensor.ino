
const int GAS_PIN = A1;

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);
}

void loop() {
  int gasRaw = analogRead(GAS_PIN);

  Serial.print("{\"timestamp_ms\":");
  Serial.print(millis());

  Serial.print(",\"gas_raw\":");
  Serial.print(gasRaw);

  Serial.println("}");

  delay(500);
}

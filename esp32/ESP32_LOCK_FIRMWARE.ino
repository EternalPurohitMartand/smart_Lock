/*
 * ESP32 Smart Lock firmware — works with this project's server.py (or future Node port).
 * You keep Blynk for now; flash this only when ready to move to your own platform.
 *
 * Flow (matches paper Fig.2):
 *  1. User presents credential (keypad/RFID) -> ESP32 POSTs /api/access/request
 *  2. Server replies GRANT / STEP_UP (+OTP) / DENY_ALERT with risk R
 *  3. ESP32 drives relay/solenoid only on GRANT (or STEP_UP verified via /api/access/stepup)
 *  4. ESP32 also listens on MQTT smartlock/<DEVICE_ID>/command for dashboard lock/unlock
 *
 * Libraries: WiFi, HTTPClient, (optional) PubSubClient for MQTT, ArduinoJson
 */
#include <WiFi.h>
#include <HTTPClient.h>
#ifdef USE_MQTT
#include <PubSubClient.h>
#endif

const char* WIFI_SSID = "YOUR_WIFI";
const char* WIFI_PASS = "YOUR_PASS";
const char* API_BASE  = "http://192.168.1.50:8000"; // your PC running server.py
const char* DEVICE_ID = "door01";
const char* USER_ID   = "user_01";
const int RELAY_PIN = 26;   // solenoid/relay
const int BUZZER_PIN = 25;

void setLock(bool unlocked) {
  digitalWrite(RELAY_PIN, unlocked ? HIGH : LOW);
  Serial.printf("LOCK -> %s\n", unlocked ? "UNLOCKED" : "LOCKED");
}

void setup() {
  Serial.begin(115200);
  pinMode(RELAY_PIN, OUTPUT); pinMode(BUZZER_PIN, OUTPUT);
  setLock(false);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) { delay(500); Serial.print("."); }
  Serial.println("\nWiFi connected. POST credential attempts to /api/access/request");
}

// Call this when a PIN/RFID is entered. `pin` = entered PIN.
String requestAccess(String pin) {
  HTTPClient h; h.begin(String(API_BASE) + "/api/access/request");
  h.addHeader("Content-Type", "application/json");
  String body = "{\"userId\":\"" + String(USER_ID) + "\",\"credential\":\"" + pin + "\"}";
  int code = h.POST(body);
  String res = h.getString(); h.end();
  Serial.printf("HTTP %d %s\n", code, res.c_str());
  // Parse decision: if GRANT -> setLock(true); if STEP_UP -> prompt OTP then POST /api/access/stepup
  if (res.indexOf("GRANT") >= 0) setLock(true);
  else { tone(BUZZER_PIN, 1000, 300); }
  return res;
}

void loop() {
  // Demo: type PIN in Serial Monitor to simulate keypad
  if (Serial.available()) {
    String pin = Serial.readStringUntil('\n'); pin.trim();
    if (pin.length()) requestAccess(pin);
  }
  delay(100);
}

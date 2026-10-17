#include <Arduino.h>
#include <Adafruit_NeoPixel.h>
#include <ArduinoJson.h>
#include "config.h"

Adafruit_NeoPixel strip(NUM_LEDS, LED_PIN, NEO_GRBW + NEO_KHZ800);

// ---- Matrix ---------------------------------------------------------------
bool keyState[NUM_KEYS] = {false};
unsigned long keyLastChange[NUM_KEYS] = {0};

// ---- Encoders --------------------------------------------------------------
// Standard quadrature transition table (previous state<<2 | current state -> delta)
static const int8_t QUAD_TABLE[16] = {
    0, -1, 1, 0,
    1, 0, 0, -1,
    -1, 0, 0, 1,
    0, 1, -1, 0};

uint8_t encPrevState[2] = {0, 0};
int encAccum[2] = {0, 0};
bool encBtnState[2] = {false, false};
unsigned long encBtnLastChange[2] = {0, 0};

// ---- Joystick ----------------------------------------------------------------
#if HAS_JOYSTICK
int joyLastX = -1000, joyLastY = -1000;
bool joySwState = false;
unsigned long joySwLastChange = 0;
unsigned long joyLastSend = 0;
#endif

void sendHello() {
  JsonDocument doc;
  doc["t"] = "hello";
  doc["fw"] = FW_NAME;
  doc["ver"] = FW_VERSION;
  doc["keys"] = NUM_KEYS;
  doc["leds"] = NUM_LEDS;
  serializeJson(doc, Serial);
  Serial.print('\n');
}

void sendKeyEvent(int id, bool down) {
  JsonDocument doc;
  doc["t"] = "key";
  doc["id"] = id;
  doc["ev"] = down ? "down" : "up";
  serializeJson(doc, Serial);
  Serial.print('\n');
}

void sendEncEvent(int id, int delta) {
  JsonDocument doc;
  doc["t"] = "enc";
  doc["id"] = id;
  doc["d"] = delta;
  serializeJson(doc, Serial);
  Serial.print('\n');
}

#if HAS_JOYSTICK
void sendJoyEvent(int x, int y) {
  JsonDocument doc;
  doc["t"] = "joy";
  doc["x"] = x;
  doc["y"] = y;
  serializeJson(doc, Serial);
  Serial.print('\n');
}
#endif

void setupMatrix() {
  for (uint8_t r = 0; r < NUM_ROWS; r++) {
    pinMode(ROW_PINS[r], OUTPUT);
    digitalWrite(ROW_PINS[r], HIGH);
  }
  for (uint8_t c = 0; c < NUM_COLS; c++) {
    pinMode(COL_PINS[c], INPUT_PULLUP);
  }
}

void scanMatrix() {
  unsigned long now = millis();
  for (uint8_t r = 0; r < NUM_ROWS; r++) {
    digitalWrite(ROW_PINS[r], LOW);
    delayMicroseconds(5);
    for (uint8_t c = 0; c < NUM_COLS; c++) {
      int id = r * NUM_COLS + c;
      bool pressed = digitalRead(COL_PINS[c]) == LOW;
      if (pressed != keyState[id] && (now - keyLastChange[id]) > DEBOUNCE_MS) {
        keyState[id] = pressed;
        keyLastChange[id] = now;
        sendKeyEvent(id, pressed);
      }
    }
    digitalWrite(ROW_PINS[r], HIGH);
  }
}

void setupEncoders() {
  for (uint8_t i = 0; i < NUM_ENCODERS; i++) {
    pinMode(ENCODERS[i].a, INPUT_PULLUP);
    pinMode(ENCODERS[i].b, INPUT_PULLUP);
    pinMode(ENCODERS[i].sw, INPUT_PULLUP);
    uint8_t a = digitalRead(ENCODERS[i].a);
    uint8_t b = digitalRead(ENCODERS[i].b);
    encPrevState[i] = (a << 1) | b;
  }
}

void scanEncoders() {
  unsigned long now = millis();
  for (uint8_t i = 0; i < NUM_ENCODERS; i++) {
    uint8_t a = digitalRead(ENCODERS[i].a);
    uint8_t b = digitalRead(ENCODERS[i].b);
    uint8_t curr = (a << 1) | b;
    uint8_t idx = (encPrevState[i] << 2) | curr;
    int8_t delta = QUAD_TABLE[idx & 0x0F];
    encPrevState[i] = curr;
    if (delta != 0) {
      encAccum[i] += delta;
      // 4 transitions per standard mechanical detent
      if (encAccum[i] >= 4) {
        sendEncEvent(i, 1);
        encAccum[i] = 0;
      } else if (encAccum[i] <= -4) {
        sendEncEvent(i, -1);
        encAccum[i] = 0;
      }
    }

    bool pressed = digitalRead(ENCODERS[i].sw) == LOW;
    if (pressed != encBtnState[i] && (now - encBtnLastChange[i]) > DEBOUNCE_MS) {
      encBtnState[i] = pressed;
      encBtnLastChange[i] = now;
      sendKeyEvent(ENCODERS[i].keyId, pressed);
    }
  }
}

#if HAS_JOYSTICK
void setupJoystick() {
  pinMode(JOY_SW_PIN, INPUT_PULLUP);
}

void scanJoystick() {
  unsigned long now = millis();
  if (now - joyLastSend >= JOY_SEND_INTERVAL_MS) {
    int x = analogRead(JOY_VRX_PIN);
    int y = analogRead(JOY_VRY_PIN);
    if (abs(x - joyLastX) >= JOY_DEADZONE_DELTA || abs(y - joyLastY) >= JOY_DEADZONE_DELTA) {
      sendJoyEvent(x, y);
      joyLastX = x;
      joyLastY = y;
    }
    joyLastSend = now;
  }

  bool pressed = digitalRead(JOY_SW_PIN) == LOW;
  if (pressed != joySwState && (now - joySwLastChange) > DEBOUNCE_MS) {
    joySwState = pressed;
    joySwLastChange = now;
    sendKeyEvent(JOY_SW_KEY_ID, pressed);
  }
}
#endif

void applyLed(int id, uint8_t r, uint8_t g, uint8_t b, uint8_t w) {
  if (id < 0 || id >= NUM_LEDS) return;
  strip.setPixelColor(id, strip.Color(r, g, b, w));
}

void handleLine(const String &line) {
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, line);
  if (err) return;

  const char *t = doc["t"];
  if (t == nullptr) return;

  if (strcmp(t, "ping") == 0) {
    sendHello();
  } else if (strcmp(t, "led") == 0) {
    applyLed(doc["id"], doc["r"], doc["g"], doc["b"], doc["w"]);
    strip.show();
  } else if (strcmp(t, "led_all") == 0) {
    uint32_t c = strip.Color(doc["r"], doc["g"], doc["b"], doc["w"]);
    for (int i = 0; i < NUM_LEDS; i++) strip.setPixelColor(i, c);
    strip.show();
  } else if (strcmp(t, "led_frame") == 0) {
    JsonArray px = doc["px"];
    int i = 0;
    for (JsonVariant v : px) {
      if (i >= NUM_LEDS) break;
      JsonArray p = v.as<JsonArray>();
      applyLed(i, p[0], p[1], p[2], p[3]);
      i++;
    }
    strip.show();
  }
}

String serialBuf;

void pollSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      handleLine(serialBuf);
      serialBuf = "";
    } else if (c != '\r') {
      serialBuf += c;
    }
  }
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  setupMatrix();
  setupEncoders();
#if HAS_JOYSTICK
  setupJoystick();
#endif
  strip.begin();
  strip.setBrightness(BRIGHTNESS);
  strip.show();
  delay(200);
  sendHello();
}

void loop() {
  scanMatrix();
  scanEncoders();
#if HAS_JOYSTICK
  scanJoystick();
#endif
  pollSerial();
}

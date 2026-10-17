#pragma once

// ---- Key matrix ------------------------------------------------
#define NUM_ROWS 4
#define NUM_COLS 3
#define NUM_KEYS (NUM_ROWS * NUM_COLS)

static const uint8_t ROW_PINS[NUM_ROWS] = {13, 14, 27, 26};
static const uint8_t COL_PINS[NUM_COLS] = {25, 33, 32};

#define DEBOUNCE_MS 12

// ---- Rotary encoders --------------------------------------------------
#define NUM_ENCODERS 1 // switch to 2 if you wire the second knob

struct EncoderPins {
  uint8_t a;
  uint8_t b;
  uint8_t sw;
  uint8_t keyId; // logical id of the encoder's button (reserved range 100+)
};

static const EncoderPins ENCODERS[2] = {
    {18, 19, 23, 100},
    {16, 17, 21, 101},
};

// ---- Flat analog joystick (5-pin PS2 module: GND, VRx, VRy, +5V, SW)
// IMPORTANT: power this module with 3V3, NOT 5V (see hardware/PINOUT.md) --
// otherwise VRx/VRy can exceed the ESP32's ADC input max voltage.
#define HAS_JOYSTICK 1
#define JOY_VRX_PIN 34
#define JOY_VRY_PIN 35
#define JOY_SW_PIN 22
#define JOY_SW_KEY_ID 110          // joystick button, reserved range 110+
#define JOY_SEND_INTERVAL_MS 40    // ~25 Hz, avoids saturating the serial port
#define JOY_DEADZONE_DELTA 24      // only sends if the value moved by at least this much (on 12 bits)

// ---- SK6812RGBW LEDs -------------------------------------------------------
// A single chain: NUM_KEYS pixels under the keys, then RING_LEDS pixels
// forming a separate status ring around the case (same data wire).
#define LED_PIN 4
#define RING_LEDS 12  // case v2: 3 per wall (4 walls)
#define NUM_LEDS (NUM_KEYS + RING_LEDS)
// No cap here: the current budget (to run on a single USB cable, with no
// external power) is applied on the Python bridge side
// (power_budget_ma in config.yaml, see agentpad/led_styles.py). The
// firmware passes through the values it receives as-is.
#define BRIGHTNESS 255

// ---- Serial link ---------------------------------------------------------
#define SERIAL_BAUD 115200

#define FW_NAME "agentpad-fw"
#define FW_VERSION "0.1.0"

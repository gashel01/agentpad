import math

from agentpad.modes.common import ActionDispatcher

COMPASS_BY_BUCKET = ["E", "NE", "N", "NW", "W", "SW", "S", "SE"]
ID_BY_COMPASS = {"N": 200, "NE": 201, "E": 202, "SE": 203, "S": 204, "SW": 205, "W": 206, "NW": 207}


class JoystickDecoder:
    """Turns the raw positions (0-4095) sent by the firmware into
    8 discrete directions + a center, and relays them to the dispatcher as
    keys (ids 200-207, see docs/PROTOCOL.md).

    The center is auto-calibrated on the very first sample received (the
    joystick is assumed at rest on startup). The invert_x/
    invert_y/deadzone settings can be adjusted in config.yaml without reflashing the firmware.
    """

    def __init__(self, dispatcher: ActionDispatcher, deadzone: int = 650,
                 invert_x: bool = False, invert_y: bool = False, center=None):
        self.dispatcher = dispatcher
        self.deadzone = deadzone
        self.invert_x = invert_x
        self.invert_y = invert_y
        # center measured by the app's calibration view (config "calibration.joystick.center");
        # otherwise auto-calibrated on the first sample, as before
        self.center = tuple(center) if center else None
        self.current_dir = None

    def on_raw(self, x: int, y: int):
        if self.center is None:
            self.center = (x, y)
            return

        dx = x - self.center[0]
        dy = y - self.center[1]
        if self.invert_x:
            dx = -dx
        if self.invert_y:
            dy = -dy

        dist = math.hypot(dx, dy)
        if dist < self.deadzone:
            new_dir = None
        else:
            deg = math.degrees(math.atan2(dy, dx)) % 360
            bucket = round(deg / 45) % 8
            compass = COMPASS_BY_BUCKET[bucket]
            new_dir = ID_BY_COMPASS[compass]

        if new_dir != self.current_dir:
            if self.current_dir is not None:
                self.dispatcher.on_key(self.current_dir, False)
            if new_dir is not None:
                self.dispatcher.on_key(new_dir, True)
            self.current_dir = new_dir

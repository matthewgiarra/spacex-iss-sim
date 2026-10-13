"""Your Dragon autopilot. Edit the Controller class below.

Run it with your autopilot URL from the Autopilot page:
    python autopilot.py https://<site>/ap/<your-key>             then press LAUNCH in the simulator tab
    python autopilot.py https://<site>/ap/<your-key> --launch    or let this program launch the flight

The simulator runs in REAL TIME: the Dragon keeps moving while your code thinks, and your inputs take
a moment to reach it. Telemetry never predates your last inputs taking effect, so you won't react
twice to the same situation.

STATE: data["state"], 12 numbers (metres, m/s, degrees, deg/s):
    [x, y, z, x_dot, y_dot, z_dot, roll, pitch, yaw, roll_dot, pitch_dot, yaw_dot]
    x, y, z    where you are relative to the docking port: x = distance along the docking axis (starts
               about 200, must reach 0), y = left/right offset (+ is right), z = up/down offset (+ is up)
    roll, pitch, yaw   0, 0, 0 means pointing straight at the port
    data["commanded_rates"]  [roll, pitch, yaw] rates the vehicle is easing toward (about 1 s lag)

CONTROL INPUTS: [x, y, z, roll, pitch, yaw], each a number of thruster pulses.
    +1 adds 0.06 m/s to that velocity (or 0.3 deg/s to that rotation rate); -1 subtracts it.
    Whole pulses only (inputs are rounded), at most 50 per axis per call. Speeds persist: no friction.
    Translations push along the vehicle's own axes, so they match x/y/z only when attitude is about 0.

TO DOCK: range < 0.2 m, roll/pitch/yaw each within 0.2 deg, each velocity under 0.24 m/s. You FAIL if
you touch the station elsewhere, drift past 500 m, take over 600 s, or break the APPROACH CORRIDOR:
closing speed must never exceed 0.1 + 0.02 * range m/s. Full details: AUTOPILOT_API.md
"""

import sys
import time

import hackathon.client as hackathon_client

STALE_MS = 2000  # skip telemetry that arrives this long after the previous one (you were stalled)

X, Y, Z, X_DOT, Y_DOT, Z_DOT, ROLL, PITCH, YAW, ROLL_DOT, PITCH_DOT, YAW_DOT = range(12)
ROTATION_PULSE = 0.3  # deg/s per rotation pulse
TRANSLATION_PULSE = 0.06  # m/s per translation pulse


class Controller:
    """This example only levels out the attitude, so the Dragon ends up pointing at the station...
    and then sits there. Making it actually dock (fast, and inside the approach corridor) is your job!"""

    ACT_EVERY_MS = 500  # only act this often, so we see the effect of each burn before the next

    def __init__(self):
        self.since_action_ms = self.ACT_EVERY_MS

    def update(self, state, commanded_rates, dt_ms):
        """Return the control inputs [x, y, z, roll, pitch, yaw] for this moment."""
        self.since_action_ms += dt_ms
        if self.since_action_ms < self.ACT_EVERY_MS:
            return [0, 0, 0, 0, 0, 0]
        self.since_action_ms = 0

        inputs = [0, 0, 0, 0, 0, 0]
        for i, angle in enumerate(state[ROLL:YAW + 1]):
            # Aim for a rotation rate that brings the angle back toward 0, slowing down as it gets close.
            target_rate = max(-3.0, min(3.0, -0.25 * angle))
            inputs[3 + i] = round((target_rate - commanded_rates[i]) / ROTATION_PULSE)
        return inputs


def main():
    urls = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not urls:
        sys.exit("Usage: python autopilot.py <your autopilot URL> [--launch]   (copy the URL from the Autopilot page)")
    connection = hackathon_client.connect(urls[0])
    if "--launch" in sys.argv:
        reply = connection.launch()
        print("Launching..." if reply["ok"] else "Couldn't launch: %s" % reply["error"])
    print("Waiting for a flight. Press LAUNCH in the simulator tab (or run with --launch).")

    controller = Controller()
    flight_id = None
    timestamp_ms = 0
    last_print_ms = -1e9
    try:
        while True:
            data = connection.request_telemetry()
            if flight_id is None:
                if data["status"] == "offline":  # the simulator tab isn't open
                    print(data["message"])
                    time.sleep(2)
                    continue
                if data["status"] != "flying":
                    time.sleep(0.05)
                    continue
                flight_id, timestamp_ms = data["flight_id"], data["timestamp_ms"]
                print("Flight %s started. Flying!" % flight_id)
                continue

            # The flight is over: docked, failed, or replaced by a new one.
            if data["status"] != "flying" or data["flight_id"] != flight_id:
                if data["docked"]:
                    print("*** DOCKED in %.1f s%s ***" % (data["timestamp_ms"] / 1000.0, " - #%d on the leaderboard" % data["rank"] if data["rank"] else ""))
                else:
                    print("--- Flight over: %s ---" % data["message"])
                break

            new_timestamp_ms = data["timestamp_ms"]
            dt = new_timestamp_ms - timestamp_ms
            if dt == 0:  # nothing new yet; don't spam the website
                time.sleep(0.010)
                continue
            timestamp_ms = new_timestamp_ms
            if dt > STALE_MS:
                continue

            state = data["state"]
            if timestamp_ms - last_print_ms >= 5000:
                last_print_ms = timestamp_ms
                print("t=%6.1fs  pos=(%6.1f,%5.1f,%5.1f) m  vel=(%5.2f,%5.2f,%5.2f) m/s  att=(%5.1f,%5.1f,%5.1f) deg" % (
                    (timestamp_ms / 1000.0,) + tuple(state[X:Z_DOT + 1]) + tuple(state[ROLL:YAW + 1])))

            inputs = controller.update(state, data["commanded_rates"], dt)
            if any(inputs):
                reply = connection.send_control_inputs(inputs)
                if not reply["ok"]:
                    print("Control inputs not applied: %s" % reply["error"])
    except KeyboardInterrupt:
        if flight_id:
            connection.abort("stopped with Ctrl+C")
        print("\nStopped.")
    finally:
        connection.close()


if __name__ == "__main__":
    main()

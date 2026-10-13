#!/usr/bin/env python3
"""Staff test autopilot: flies a complete docking through the Autopilot (intermediate) interface.

Use it to check the whole system before the event: the website relay, scoring and the leaderboard.
It uses the starter kit's hackathon.client, exactly as a participant's autopilot would.

    1. Open the site, choose Autopilot, type a callsign, press GET AUTOPILOT LINK. Keep the tab visible.
    2. python hackathon/tools/test_autopilot.py <autopilot URL> --launch

It docks in roughly 5 to 6 minutes of real time, staying inside the approach corridor. Standard library only.
Keep this out of the participant kit: it is a working answer.
"""

import argparse
import math
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kit"))
import hackathon.client as hackathon_client  # noqa: E402

ROT_PULSE = 0.3  # deg/s per rotation pulse
TRANS_PULSE = 0.06  # m/s per translation pulse
RATE_LAG_S = 0.33  # rotation rates ease toward the commanded value with about this time constant
ACT_EVERY_MS = 300  # decide at most this often (sim time)
CORRIDOR_MARGIN = 0.6  # fly at this fraction of the corridor speed limit
X, Y, Z, X_DOT, Y_DOT, Z_DOT, ROLL, PITCH, YAW = range(9)


def corridor_limit(range_m):
    return 0.1 + 0.02 * range_m


def control(state, commanded_rates):
    """Pulses [x, y, z, roll, pitch, yaw] for this moment. Attitude first; translate only once pointed at the port."""
    inputs = [0] * 6
    for i, (angle, commanded) in enumerate(zip(state[ROLL:YAW + 1], commanded_rates)):
        predicted = angle + commanded * RATE_LAG_S  # where the angle is heading
        want = 0 if abs(predicted) < 0.08 else -math.copysign(max(1, min(10, round(abs(predicted) * 0.3 / ROT_PULSE))), predicted)
        inputs[3 + i] = want - round(commanded / ROT_PULSE)

    if all(abs(a) < 0.5 for a in state[ROLL:YAW + 1]):
        p, v = state[X:Z + 1], state[X_DOT:Z_DOT + 1]
        want = [0, 0, 0]
        for i in (Y, Z):  # centre up sideways and vertically; faster when far off, one pulse when close
            want[i] = 0 if abs(p[i]) < 0.04 else -math.copysign(max(1, min(8, round(abs(p[i]) * 0.1 / TRANS_PULSE))), p[i])
        lateral = max(abs(p[Y]), abs(p[Z]))
        if p[X] >= 10 or lateral <= 0.1:  # hold off the final approach until centred
            speed = min(CORRIDOR_MARGIN * corridor_limit(math.sqrt(sum(c * c for c in p))), max(0.12, 0.04 * p[X]))
            want[X] = -max(1, round(speed / TRANS_PULSE))
        for i in (X, Y, Z):
            inputs[i] = want[i] - round(v[i] / TRANS_PULSE)
    return inputs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", help="autopilot URL from the Autopilot page")
    ap.add_argument("--launch", action="store_true", help="start the flight from here instead of the LAUNCH button")
    args = ap.parse_args()

    connection = hackathon_client.connect(args.url)
    if args.launch:
        res = connection.launch()
        if not res["ok"]:
            sys.exit("Couldn't launch: %s" % res["error"])
        print("Launched; waiting for the Dragon to arrive...")
    else:
        print("Waiting for LAUNCH in the simulator tab...")

    flight_id, last_ts, last_action, last_print, sent, started = None, 0, -1e9, -1e9, 0, time.time()
    try:
        while True:
            data = connection.request_telemetry()
            if flight_id is None:
                if data["status"] == "offline":
                    sys.exit(data["message"])
                if data["status"] == "failed" and args.launch:  # our launch didn't get off the ground
                    sys.exit("Launch failed: %s" % data["message"])
                if data["status"] != "flying":
                    time.sleep(0.05)
                    continue
                flight_id, started = data["flight_id"], time.time()
                print("Flight %s started." % flight_id)
            if data["flight_id"] != flight_id or data["status"] in ("docked", "failed"):
                print("\nRESULT: %s after %.1f s of sim time (%.0f s real time). %s%s" % (
                    data["status"].upper(), data["timestamp_ms"] / 1000.0, time.time() - started, data["message"],
                    (" Rank #%d." % data["rank"]) if data["rank"] else ""))
                return 0 if data["docked"] else 1
            ts = data["timestamp_ms"]
            if ts == last_ts or ts - last_action < ACT_EVERY_MS:
                time.sleep(0.01)
                continue
            last_ts = last_action = ts
            s = data["state"]
            if ts - last_print >= 10000:
                last_print = ts
                rng = math.sqrt(s[X] ** 2 + s[Y] ** 2 + s[Z] ** 2)
                closing = -(s[X] * s[X_DOT] + s[Y] * s[Y_DOT] + s[Z] * s[Z_DOT]) / rng if rng else 0
                print("t=%5.0fs  range=%7.2f m  closing=%5.2f m/s (limit %4.2f)  att=(%5.2f,%5.2f,%5.2f)  pulses sent=%d" % (
                    ts / 1000.0, rng, closing, corridor_limit(rng), s[ROLL], s[PITCH], s[YAW], sent))
            inputs = control(s, data["commanded_rates"])
            if any(inputs):
                res = connection.send_control_inputs(inputs)
                if res["ok"]:
                    sent += sum(abs(n) for n in res["pulses"])
                elif "no flight" not in res["error"]:  # the flight just ended; the next telemetry says how
                    print("  control failed: %s" % res["error"])
    except KeyboardInterrupt:
        if flight_id:
            connection.abort("test autopilot stopped with Ctrl+C")
        print("\nStopped.")
        return 1


if __name__ == "__main__":
    sys.exit(main())

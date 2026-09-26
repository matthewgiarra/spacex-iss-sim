"""Reference autopilot: a plain feedback controller (no AI) that docks via the HTTP API.

It exists to prove the API is sufficient to dock, and as a baseline to beat.

    python server.py                  # in one terminal, then open http://localhost:5555
    python examples/autopilot.py      # in another

By default it pauses the sim and advances it in fixed steps (--realtime to fly live).
"""

import argparse
import math
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from dragon import Dragon  # noqa: E402

ROT_PULSE = 0.3  # deg/s per fine rotation pulse
TRANS_PULSE = 0.06  # m/s per fine translation pulse
RATE_LAG_S = 0.33  # rotation rates ease toward the commanded value with about this time constant

# Positive pulses increase the value, negative pulses decrease it.
ROTATION_CMDS = {"roll": ("roll_left", "roll_right"), "pitch": ("pitch_up", "pitch_down"), "yaw": ("yaw_left", "yaw_right")}
# With attitude near zero the vehicle axes line up with the position axes.
TRANSLATION_CMDS = {"x": ("translate_backward", "translate_forward"), "y": ("translate_right", "translate_left"), "z": ("translate_up", "translate_down")}


def pulses(axis_cmds, n):
    n = max(-50, min(50, n))
    if n == 0:
        return []
    up, down = axis_cmds
    return [{"command": up if n > 0 else down, "count": abs(n)}]


def attitude_commands(s):
    cmds = []
    for axis, names in ROTATION_CMDS.items():
        commanded = s["rotation_rate_commanded"][axis]
        predicted = s["attitude"][axis] + commanded * RATE_LAG_S  # where we'll coast to
        if abs(predicted) < 0.08:
            want = 0
        else:
            want = -math.copysign(max(1, min(10, round(abs(predicted) * 0.3 / ROT_PULSE))), predicted)
        cmds += pulses(names, int(want - round(commanded / ROT_PULSE)))
    return cmds


def translation_commands(s):
    p, v = s["position"], s["velocity"]
    lateral_error = max(abs(p["y"]), abs(p["z"]))
    want = {}
    for axis in ("y", "z"):
        # Creep at one pulse when close, faster when far.
        want[axis] = 0 if abs(p[axis]) < 0.04 else -math.copysign(max(1, min(8, round(abs(p[axis]) * 0.1 / TRANS_PULSE))), p[axis])
    if p["x"] < 10 and lateral_error > 0.1:
        want["x"] = 0  # line up before the final approach
    else:
        want["x"] = -max(2, min(25, round(p["x"] * 0.04 / TRANS_PULSE)))  # 2 pulses = 0.12 m/s at contact
    cmds = []
    for axis, names in TRANSLATION_CMDS.items():
        cmds += pulses(names, int(want[axis] - round(v[axis] / TRANS_PULSE)))
    return cmds


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://localhost:5555")
    ap.add_argument("--dt", type=float, default=0.2, help="seconds of sim time between control updates")
    ap.add_argument("--realtime", action="store_true", help="fly in real time instead of pause-and-step")
    args = ap.parse_args()

    sim = Dragon(args.url)
    print("resetting...")
    s = sim.reset()
    if args.realtime:
        sim.resume()
    else:
        sim.pause()

    last_print = -10
    while s["status"] == "flying":
        cmds = attitude_commands(s)
        aligned = all(abs(a) < 0.5 for a in s["attitude"].values())
        if aligned:
            cmds += translation_commands(s)
        if args.realtime:
            if cmds:
                sim.commands(*cmds)
            time.sleep(args.dt)
            s = sim.state()
        else:
            s = sim.commands(*cmds, {"command": "step", "seconds": args.dt})
        if s["time_s"] - last_print >= 5 or s["status"] != "flying":
            last_print = s["time_s"]
            print("t=%6.1fs  range=%7.2f m  pos=%s  vel=%s  att=%s" % (s["time_s"], s.get("range", 0), s.get("position"), s.get("velocity"), s.get("attitude")))

    sim.resume()
    print("RESULT:", s["status"], "-", s["message"])
    return 0 if s["status"] == "success" else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Fly your autopilot on the Dragon Docking Challenge.

    python run.py https://<site>/ap/<your-key>                 # then press LAUNCH in the browser
    python run.py https://<site>/ap/<your-key> --launch        # or launch from here

The simulator runs in real time and streams telemetry up to 20 times a second. Each time new
telemetry arrives, this calls control(state) in autopilot.py and sends whatever commands it
returns. If control() is still busy when newer telemetry arrives, the stale readings are skipped:
your autopilot always sees the latest state. After sending commands, it also skips telemetry from
before they took effect, so control() never reacts twice to the same situation. autopilot.py is reloaded at the start of every flight,
so you can edit it, save, and press LAUNCH again without restarting this program.

This kit is optional: any language can do the same over HTTP (see AUTOPILOT_API.md).
Standard library only (Python 3.8+).
"""

import argparse
import importlib.util
import json
import os
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class Link:
    def __init__(self, base):
        self.base = base.rstrip("/")

    def call(self, method, path, body=None, timeout=20):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            try:
                msg = json.load(e).get("error", str(e))
            except ValueError:
                msg = str(e)
            raise ApiError(e.code, msg) from None
        except (urllib.error.URLError, OSError) as e:
            raise ApiError(0, "can't reach %s: %s" % (self.base, getattr(e, "reason", e))) from None

    def events(self):
        """Yield (event, data) from the telemetry stream forever, reconnecting if it drops."""
        delay = 1
        while True:
            try:
                req = urllib.request.Request(self.base + "/api/telemetry", headers={"Accept": "text/event-stream"})
                with urllib.request.urlopen(req, timeout=60) as r:  # the server sends a keepalive every 15 s
                    delay = 1
                    event, data = None, []
                    for raw in r:
                        line = raw.decode("utf-8").rstrip("\r\n")
                        if line.startswith("event:"):
                            event = line[6:].strip()
                        elif line.startswith("data:"):
                            data.append(line[5:].strip())
                        elif line == "" and event:
                            yield event, json.loads("\n".join(data))
                            event, data = None, []
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    yield "fatal", {"error": "unknown or expired autopilot URL; get a new one from the Autopilot page"}
                    return
            except (urllib.error.URLError, OSError, ValueError):
                pass
            yield "disconnected", {"retry_in": delay}
            time.sleep(delay)
            delay = min(10, delay * 2)


class Mailbox:
    """Hands the newest telemetry (and every lifecycle event) from the reader thread to the main loop."""

    def __init__(self):
        self.cond = threading.Condition()
        self.lifecycle = []
        self.telemetry = None

    def put(self, event, data):
        with self.cond:
            if event == "telemetry":
                self.telemetry = data  # overwrite: only the newest reading matters
            else:
                self.lifecycle.append((event, data))
            self.cond.notify()

    def get(self):
        with self.cond:
            while not self.lifecycle and self.telemetry is None:
                self.cond.wait()
            if self.lifecycle:
                return self.lifecycle.pop(0)
            data, self.telemetry = self.telemetry, None
            return "telemetry", data


def load_autopilot(path):
    """Import autopilot.py fresh, so edits take effect on the next flight."""
    spec = importlib.util.spec_from_file_location("autopilot_%d" % int(time.time() * 1000), path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.path.dirname(os.path.abspath(path)))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    if not callable(getattr(module, "control", None)):
        raise RuntimeError("%s must define a function control(state)" % path)
    return module


def normalize(result):
    """control() may return a list of "name", ("name", count) or {"command": "name", "count": n}."""
    commands = []
    for c in result or []:
        if isinstance(c, str):
            commands.append({"command": c})
        elif isinstance(c, (tuple, list)) and len(c) == 2:
            commands.append({"command": c[0], "count": int(c[1])})
        elif isinstance(c, dict):
            commands.append(c)
        else:
            raise ValueError("can't understand command %r; use \"pitch_up\", (\"pitch_up\", 3) or {\"command\": \"pitch_up\", \"count\": 3}" % (c,))
    return commands


def describe(s):
    p, v, a = s.get("position", {}), s.get("velocity", {}), s.get("attitude", {})
    return "t=%6.1fs  range=%7.2f m  closing=%5.2f m/s  pos=(%6.1f,%5.1f,%5.1f)  vel=(%5.2f,%5.2f,%5.2f)  att=(%5.1f,%5.1f,%5.1f)" % (
        s.get("time_s", 0), s.get("range", 0), -s.get("range_rate", 0), p.get("x", 0), p.get("y", 0), p.get("z", 0),
        v.get("x", 0), v.get("y", 0), v.get("z", 0), a.get("roll", 0), a.get("pitch", 0), a.get("yaw", 0))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", help="your autopilot URL from the Autopilot page, e.g. https://<site>/ap/AbC123...")
    ap.add_argument("--autopilot", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "autopilot.py"),
                    help="file that defines control(state) (default: autopilot.py next to this script)")
    ap.add_argument("--launch", action="store_true", help="launch flights from this terminal instead of the browser")
    ap.add_argument("--once", action="store_true", help="exit after one flight")
    ap.add_argument("--verbose", action="store_true", help="print every command your autopilot sends")
    args = ap.parse_args()

    link = Link(args.url)
    try:
        info = link.call("GET", "/api")
    except ApiError as e:
        sys.exit("Couldn't connect: %s\nCheck the URL (copy it from the Autopilot page)." % e)
    print("Connected as %s. Autopilot file: %s" % (info.get("callsign"), args.autopilot))

    mailbox = Mailbox()
    threading.Thread(target=lambda: [mailbox.put(ev, d) for ev, d in link.events()], daemon=True).start()

    def launch():
        try:
            link.call("POST", "/api/launch")
            print("Launching... (the Dragon takes a few seconds to arrive)")
        except ApiError as e:
            print("Couldn't launch: %s" % e)

    if args.launch:
        launch()
    else:
        print("Waiting for launch. Press LAUNCH in the simulator tab (or run with --launch).")

    flight, autopilot, last_print = None, None, -1e9
    applied_at = -1.0  # flight time our last commands took effect; older telemetry doesn't show them yet
    try:
        while True:
            event, data = mailbox.get()
            if event == "fatal":
                sys.exit(data["error"])
            elif event == "disconnected":
                print("  (telemetry stream dropped; reconnecting in %d s)" % data["retry_in"])
            elif event == "hello":
                if data.get("flight") and data["flight"]["status"] == "launching":
                    print("A flight is launching...")
            elif event == "flight_start":
                flight = data["flight_id"]
                try:
                    autopilot = load_autopilot(args.autopilot)
                    if callable(getattr(autopilot, "reset", None)):
                        autopilot.reset()
                except Exception:
                    traceback.print_exc()
                    link.call("POST", "/api/abort", {"flight_id": flight, "reason": "autopilot.py failed to load"})
                    print("\nFix autopilot.py, save, and launch again.")
                    flight = None
                    continue
                last_print, applied_at = -1e9, -1.0
                print("Flight %s started. Flying with %s" % (flight, os.path.basename(args.autopilot)))
            elif event == "telemetry" and flight and data["flight_id"] == flight:
                state = data["state"]
                if state.get("time_s", 0) - last_print >= 5:
                    print(describe(state))
                    last_print = state.get("time_s", 0)
                if state.get("time_s", 0) <= applied_at:
                    continue
                try:
                    commands = normalize(autopilot.control(state))
                except Exception:
                    print("\nYour control() raised an error at t=%.1f s with this state:\n%s\n" % (state.get("time_s", 0), json.dumps(state)))
                    traceback.print_exc()
                    try:
                        link.call("POST", "/api/abort", {"flight_id": flight, "reason": "control() raised an error"})
                    except ApiError:
                        pass
                    print("\nFlight aborted. Fix autopilot.py, save, and launch again.")
                    flight = None
                    continue
                if commands:
                    try:
                        res = link.call("POST", "/api/command", {"flight_id": flight, "commands": commands})
                        applied_at = res.get("time_s") or applied_at
                        if args.verbose:
                            print("  t=%6.1fs  sent %s" % (state.get("time_s", 0), ", ".join("%s x%d" % (c["command"], c.get("count", 1)) for c in commands)))
                    except ApiError as e:
                        if e.status == 400:
                            print("\nThe server rejected what control() returned: %s\nCommands were: %s" % (e, json.dumps(commands)))
                            link.call("POST", "/api/abort", {"flight_id": flight, "reason": "invalid commands: %s" % e})
                            print("Flight aborted. Fix autopilot.py, save, and launch again.")
                            flight = None
                        elif e.status != 409:  # 409: the flight just ended; flight_end is on its way
                            print("  command not delivered: %s" % e)
            elif event == "flight_end":
                res = data["result"]
                if res["outcome"] == "success":
                    print("\n*** DOCKED in %.1f s%s ***\n" % (res["time_s"], (" - #%d on the leaderboard" % res["rank"]) if res.get("rank") else ""))
                elif res["outcome"] == "fail":
                    print("\n--- FAILED after %.1f s: %s ---\n" % (res["time_s"], res["message"]))
                else:
                    print("\n--- Flight didn't start: %s ---\n" % res["message"])
                flight = None
                if args.once:
                    break
                if args.launch:
                    input("Press Enter to fly again (Ctrl+C to quit). ")
                    launch()
                else:
                    print("Waiting for the next launch.")
    except KeyboardInterrupt:
        if flight:
            try:
                link.call("POST", "/api/abort", {"flight_id": flight, "reason": "stopped with Ctrl+C"}, timeout=5)
            except ApiError:
                pass
        print("\nBye.")


if __name__ == "__main__":
    main()

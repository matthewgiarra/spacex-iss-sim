#!/usr/bin/env python3
"""Serve the ISS docking simulator and expose it as a JSON HTTP API.

    python server.py            # then open http://localhost:5555 in a browser

The simulation runs in the browser tab. This server relays between the tab and API
clients: the page posts its state here every ~30 ms and picks up queued commands.

    GET  /api/state      current vehicle state
    POST /api/command    {"command": "pitch_up", "count": 2}  (or a JSON list of these)
    GET  /api            list of commands

See API.md for details. Standard library only; no installs needed.
"""

import argparse
import itertools
import json
import os
import socket
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

SITE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "iss-sim.spacex.com")
BROWSER_TIMEOUT_S = 3.0  # no sync from the page for this long => treat it as disconnected
COMMAND_TIMEOUT_S = 90.0  # "reset" waits for the ~10 s arrival animation

COMMANDS = {
    "roll_left": "Rotation pulse. Changes roll rate. Optional 'count' (1-50).",
    "roll_right": "Rotation pulse. Changes roll rate. Optional 'count' (1-50).",
    "pitch_up": "Rotation pulse. Changes pitch rate. Optional 'count' (1-50).",
    "pitch_down": "Rotation pulse. Changes pitch rate. Optional 'count' (1-50).",
    "yaw_left": "Rotation pulse. Changes yaw rate. Optional 'count' (1-50).",
    "yaw_right": "Rotation pulse. Changes yaw rate. Optional 'count' (1-50).",
    "translate_forward": "Translation pulse along the vehicle's own axes. Optional 'count' (1-50).",
    "translate_backward": "Translation pulse along the vehicle's own axes. Optional 'count' (1-50).",
    "translate_left": "Translation pulse along the vehicle's own axes. Optional 'count' (1-50).",
    "translate_right": "Translation pulse along the vehicle's own axes. Optional 'count' (1-50).",
    "translate_up": "Translation pulse along the vehicle's own axes. Optional 'count' (1-50).",
    "translate_down": "Translation pulse along the vehicle's own axes. Optional 'count' (1-50).",
    "translation_fine": "Translation pulses of 0.06 m/s (default).",
    "translation_coarse": "Translation pulses of 0.30 m/s.",
    "rotation_fine": "Rotation pulses of 0.3 deg/s (default).",
    "rotation_coarse": "Rotation pulses of 0.6 deg/s.",
    "gravity_on": "Enable the slow downward drift (default).",
    "gravity_off": "Disable the slow downward drift.",
    "reset": "Start a new attempt from any state. Returns once the vehicle is controllable.",
    "pause": "Freeze simulation time.",
    "resume": "Unfreeze simulation time.",
    "step": "While paused, advance sim time by 'seconds' (default 1, max 60).",
}


class Bridge:
    """Thread-safe mailbox between HTTP API clients and the browser tab."""

    def __init__(self):
        self.cond = threading.Condition()
        self.state = None
        self.last_sync = 0.0
        self.outbox = []  # commands waiting for the browser to pick up
        self.results = {}  # command id -> result from the browser
        self.ids = itertools.count(1)

    def connected(self):
        return time.time() - self.last_sync < BROWSER_TIMEOUT_S

    def sync(self, state, results):
        """Called by the browser: store its state/results, hand back pending commands."""
        with self.cond:
            self.state = state
            self.last_sync = time.time()
            for r in results:
                self.results[r.pop("id")] = r
            commands, self.outbox = self.outbox, []
            self.cond.notify_all()
            return commands

    def run(self, commands):
        """Queue commands and block until the browser reports each one's result."""
        with self.cond:
            ids = []
            for c in commands:
                c = dict(c, id=next(self.ids))
                ids.append(c["id"])
                self.outbox.append(c)
            deadline = time.time() + COMMAND_TIMEOUT_S
            while not all(i in self.results for i in ids):
                remaining = deadline - time.time()
                if remaining <= 0 or not self.connected():
                    self.outbox = [c for c in self.outbox if c["id"] not in ids]
                    for i in ids:
                        self.results.pop(i, None)
                    raise TimeoutError("the simulator tab stopped responding (is it open and visible?)")
                self.cond.wait(min(remaining, 0.5))
            return [self.results.pop(i) for i in ids]


bridge = Bridge()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=SITE_DIR, **kwargs)

    def log_message(self, fmt, *args):
        if "/bridge/" not in self.path:  # the page syncs ~30x/s; don't spam the console
            super().log_message(fmt, *args)

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"null")

    def no_browser(self):
        self.send_json(
            {"ok": False, "error": "No simulator connected. Open http://localhost:%d in a browser and keep the tab visible." % self.server.server_port},
            503,
        )

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/")
        if path == "/api":
            self.send_json({"endpoints": ["GET /api/state", "POST /api/command"], "commands": COMMANDS, "docs": "See API.md"})
        elif path == "/api/state":
            if not bridge.connected():
                return self.no_browser()
            self.send_json(dict(bridge.state, connected=True))
        else:
            super().do_GET()

    def do_POST(self):
        path = self.path.split("?")[0].rstrip("/")
        try:
            body = self.read_json()
        except ValueError:
            return self.send_json({"ok": False, "error": "request body must be JSON"}, 400)

        if path == "/bridge/sync":
            commands = bridge.sync(body.get("state"), body.get("results", []))
            return self.send_json({"commands": commands})

        if path == "/api/command":
            single = isinstance(body, dict)
            commands = [body] if single else body
            if not isinstance(commands, list) or not all(isinstance(c, dict) and isinstance(c.get("command"), str) for c in commands):
                return self.send_json({"ok": False, "error": 'body must be {"command": "<name>", ...} or a list of those'}, 400)
            if not bridge.connected():
                return self.no_browser()
            try:
                results = bridge.run(commands)
            except TimeoutError as e:
                return self.send_json({"ok": False, "error": str(e)}, 504)
            return self.send_json(results[0] if single else results)

        self.send_json({"ok": False, "error": "not found"}, 404)


class IPv6Server(ThreadingHTTPServer):
    address_family = socket.AF_INET6


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=5555)
    parser.add_argument("--host", help="interface to listen on; use 0.0.0.0 to allow other machines on the network (default: localhost only)")
    args = parser.parse_args()

    if args.host:
        servers = [(IPv6Server if ":" in args.host else ThreadingHTTPServer)((args.host, args.port), Handler)]
    else:
        # Listen on both loopback addresses. Many clients try IPv6 (::1) first for "localhost",
        # and on Windows falling back to IPv4 costs ~2 s per request.
        servers = [ThreadingHTTPServer(("127.0.0.1", args.port), Handler)]
        try:
            servers.append(IPv6Server(("::1", args.port), Handler))
        except OSError:
            pass
    for extra in servers[1:]:
        threading.Thread(target=extra.serve_forever, daemon=True).start()

    print("Simulator:  http://localhost:%d   (open this in a browser and keep the tab visible)" % args.port)
    print("API:        http://localhost:%d/api/state" % args.port)
    try:
        servers[0].serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

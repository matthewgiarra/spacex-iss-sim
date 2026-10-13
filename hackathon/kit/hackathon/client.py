"""hackathon.client: connect your autopilot to the simulator. Standard library only (Python 3.8+).

    import hackathon.client as hackathon_client

    connection = hackathon_client.connect("https://<site>/ap/<your-key>")    # your autopilot URL
    data = hackathon_client.request_telemetry(connection)                     # or connection.request_telemetry()
    hackathon_client.send_control_inputs(connection, [0, 0, -2, 0, 1, 0])

TELEMETRY (a dict):
    data["status"]           "flying" while you have control; otherwise "waiting", "launching", "docked",
                             "failed", or "offline" (the simulator tab isn't open)
    data["docked"]           True once you've docked
    data["flight_id"]        changes with every flight
    data["timestamp_ms"]     simulator time in this flight, in ms. Unchanged = nothing new to see yet
    data["state"]            12 numbers, or None when there's no flight:
                               [x, y, z, x_dot, y_dot, z_dot, roll, pitch, yaw, roll_dot, pitch_dot, yaw_dot]
                               metres, m/s, degrees and deg/s; position is relative to the docking port
    data["commanded_rates"]  [roll, pitch, yaw] deg/s the rotation rates are easing toward
    data["rank"]             your leaderboard place after docking
    data["message"]          why a flight ended, or what's going on

CONTROL INPUTS: 6 numbers [x, y, z, roll, pitch, yaw], each a number of thruster pulses. A positive pulse
adds 0.06 m/s to that velocity (or 0.3 deg/s to that rotation rate), a negative one subtracts it. Values are
rounded to whole pulses and limited to +/-50 per axis. Velocities persist: there's no friction.

Every function returns the reply as a dict with "ok" (and "error" when ok is False). HackathonError means
the request didn't get through at all (no network, wrong URL, expired link). Full reference: AUTOPILOT_API.md.
"""

import http.client
import json
import math
import secrets
import ssl
import urllib.parse

STATE_NAMES = ("x", "y", "z", "x_dot", "y_dot", "z_dot", "roll", "pitch", "yaw", "roll_dot", "pitch_dot", "yaw_dot")
INPUT_NAMES = ("x", "y", "z", "roll", "pitch", "yaw")


class HackathonError(Exception):
    """The request didn't get through: no network, a wrong or expired autopilot URL, or a timeout."""


class Connection:
    """A request-reply link to your simulator, through the challenge website. Keeps its connection open
    between requests, so each request costs one network round trip."""

    def __init__(self, url, timeout_ms=10000):
        parts = urllib.parse.urlsplit(url.strip())
        if parts.scheme not in ("http", "https") or not parts.hostname or "/ap/" not in parts.path:
            raise ValueError("expected your autopilot URL from the Autopilot page, like https://<site>/ap/<your-key>; got %r" % url)
        self.url = url.strip().rstrip("/")
        self._https = parts.scheme == "https"
        self._host, self._port = parts.hostname, parts.port
        self._path = parts.path.rstrip("/") + "/api/request"
        self.timeout_ms = timeout_ms
        self.client_id = secrets.token_hex(8)  # tells the website which requests come from this run of your program
        self._http = None

    def request(self, message):
        """Send one request (a dict) and return the reply (a dict)."""
        data = json.dumps(dict(message, client=self.client_id)).encode()
        for attempt in (1, 2):
            reused = self._http is not None
            if not reused:
                cls = http.client.HTTPSConnection if self._https else http.client.HTTPConnection
                self._http = cls(self._host, self._port, timeout=self.timeout_ms / 1000.0)
            try:
                self._http.request("POST", self._path, data, {"Content-Type": "application/json"})
                response = self._http.getresponse()
                raw = response.read()
                break
            except (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError) as e:
                self._drop()
                if reused and attempt == 1:
                    continue  # the website closed an idle connection before reading the request: send it again
                raise HackathonError("lost the connection to %s: %s" % (self.url, e)) from None
            except ssl.SSLCertVerificationError as e:
                self._drop()
                raise HackathonError("couldn't verify the website's certificate (%s). On a Mac with Python from python.org, "
                                     "run 'Install Certificates.command' in your Python folder." % e.reason) from None
            except (OSError, http.client.HTTPException) as e:
                self._drop()
                raise HackathonError("couldn't reach %s: %s" % (self.url, e or type(e).__name__)) from None
        try:
            reply = json.loads(raw.decode("utf-8"))
        except ValueError:
            reply = None
        if response.status != 200 or not isinstance(reply, dict) or "ok" not in reply:
            msg = reply.get("error") if isinstance(reply, dict) else None
            if response.status == 404:
                msg = "unknown or expired autopilot URL; press GET AUTOPILOT LINK on the Autopilot page for a new one"
            raise HackathonError(msg or "HTTP %d from %s" % (response.status, self.url))
        return reply

    def request_telemetry(self):
        """The latest telemetry. It never predates your last control inputs taking effect."""
        return self.request({"request": "telemetry"})

    def send_control_inputs(self, inputs):
        """Fire thrusters: inputs = [x, y, z, roll, pitch, yaw] pulses (lists, tuples and numpy arrays all work)."""
        values = [float(v) for v in inputs]
        if len(values) != 6 or not all(math.isfinite(v) for v in values):
            raise ValueError("control inputs must be 6 finite numbers [x, y, z, roll, pitch, yaw]; got %r" % (inputs,))
        return self.request({"request": "control", "inputs": values})

    def launch(self):
        """Start a flight, as the LAUNCH button does. The Dragon takes a few seconds to arrive."""
        return self.request({"request": "launch"})

    def abort(self, reason=""):
        """End the current flight (it counts as a failed attempt)."""
        return self.request({"request": "abort", "reason": reason})

    def _drop(self):
        if self._http is not None:
            self._http.close()
            self._http = None

    def close(self):
        self._drop()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def connect(url, timeout_ms=10000):
    """Connect to your simulator. url is your autopilot URL from the Autopilot page."""
    return Connection(url, timeout_ms)


def request_telemetry(connection):
    return connection.request_telemetry()


def send_control_inputs(connection, inputs):
    return connection.send_control_inputs(inputs)


def launch(connection):
    return connection.launch()


def abort(connection, reason=""):
    return connection.abort(reason)

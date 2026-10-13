"""Remote flights: a participant's own program flies the simulator in their browser tab, through this server.

    participant's program ──HTTP──▶ app.py (a RemoteSession) ──Bridge relay──▶ their sim tab

Two kinds share the plumbing:
  BlackBoxMission  advanced tier. Opaque, scrambled thrusters; position/attitude-only "sensors";
                   the agent owns the clock (pause/step/reset); score = sim time across all attempts.
  AutopilotLink    intermediate tier. Real time, request-reply: the program asks for the newest
                   telemetry and sends thruster inputs whenever it likes. One score per flight.
                   The kit's client libraries wrap it (see AUTOPILOT_API.md).
"""

import math
import random
import secrets
import threading
import time

from server import Bridge  # the same relay the local server.py uses

THRUSTERS = ["translate_forward", "translate_backward", "translate_left", "translate_right", "translate_up", "translate_down",
             "roll_left", "roll_right", "pitch_up", "pitch_down", "yaw_left", "yaw_right"]

NOT_CONNECTED = "The simulator tab for this session isn't connected. Open its page in a browser and keep it visible."


class HttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class RemoteSession:
    """A private relay between one participant's program and one browser tab."""

    def __init__(self, store, callsign):
        self.id = secrets.token_urlsafe(12)  # also the credential in the program's URL
        self.created = time.time()
        self.store = store
        self.callsign = callsign
        self.bridge = Bridge()
        self.lock = threading.Lock()  # one program request at a time

    def require_tab(self):
        if not self.bridge.connected():
            raise HttpError(503, NOT_CONNECTED)

    def sync(self, state, results):
        """The browser tab's ~30 ms heartbeat: store its state, hand back queued commands."""
        commands = self.bridge.sync(state, results)
        self.observe(state)
        return commands

    def observe(self, state):
        pass


# ---------------------------------------------------------------------------
# Advanced: Black Box Dragon
# ---------------------------------------------------------------------------

BLACKBOX_DEAD_THRUSTERS = 2
BLACKBOX_NAMES = ["T%02d" % i for i in range(1, len(THRUSTERS) + BLACKBOX_DEAD_THRUSTERS + 1)]
# Agents get "sensor" readings only: where they are and how they're pointed. No velocities or
# rates, which would reveal a thruster's effect the instant it fires (so it could be identified
# while paused, at no cost). Effects show up only as time passes and must be estimated.
AGENT_STATE_KEYS = ("status", "message", "paused", "time_s", "position", "range", "attitude")
AGENT_COMMANDS = {
    "T01 ... %s" % BLACKBOX_NAMES[-1]: "Thrusters. What each does is unknown. Optional 'count' (1-10).",
    "reset": "Start a new attempt (the mission clock keeps running across attempts).",
    "pause": "Freeze simulation time.",
    "resume": "Unfreeze simulation time.",
    "step": "While paused, advance sim time by 'seconds' (default 1, max 60).",
}


class BlackBoxMission(RemoteSession):
    def __init__(self, store, callsign):
        super().__init__(store, callsign)
        rng = random.SystemRandom()
        names = list(BLACKBOX_NAMES)
        rng.shuffle(names)
        self.mapping = {name: (real, rng.choice((1, 2, 3))) for name, real in zip(names, THRUSTERS + [None] * BLACKBOX_DEAD_THRUSTERS)}
        self.banked_s = 0.0  # sim time of finished attempts
        self.attempt_s = 0.0  # sim time of the current attempt
        self.attempt_t0 = 0.0  # page time_s when the current attempt started
        self.attempts = 0
        self.commands = 0
        self.started = False
        self.resetting = False
        self.complete = False
        self.result = None

    def mission_time(self):
        return self.banked_s + self.attempt_s

    def status(self):
        s = self.bridge.state or {}
        return {"callsign": self.callsign, "connected": self.bridge.connected(), "attempts": self.attempts,
                "mission_time_s": round(self.mission_time(), 2), "status": s.get("status"), "commands": self.commands,
                "complete": self.complete, "result": self.result}

    def observe(self, state):
        """Keep the mission clock and catch the docking."""
        if not state or not self.started or self.resetting or self.complete:
            return
        if state.get("status") in ("flying", "success", "fail") and "time_s" in state:
            self.attempt_s = max(0.0, float(state["time_s"]) - self.attempt_t0)
        if state.get("status") == "success":
            self.complete = True
            total = round(self.mission_time(), 1)
            self.store.add_run(tier="advanced", callsign=self.callsign, model="", outcome="success", time_s=total,
                               attempts=self.attempts, commands=self.commands, message="")
            self.result = {"time_s": total, "rank": self.store.rank("advanced", total)}

    def agent_state(self, state):
        s = {k: v for k, v in (state or {}).items() if k in AGENT_STATE_KEYS}
        if "time_s" in s:
            s["time_s"] = round(max(0.0, s["time_s"] - self.attempt_t0), 3)
        s["mission_time_s"] = round(self.mission_time(), 3)
        s["mission_complete"] = self.complete
        return s

    def command(self, body):
        single = isinstance(body, dict)
        cmds = [body] if single else body
        if not isinstance(cmds, list) or not cmds or len(cmds) > 50 or not all(isinstance(c, dict) and isinstance(c.get("command"), str) for c in cmds):
            raise HttpError(400, 'body must be {"command": "<name>", ...} or a list of those')
        self.require_tab()
        with self.lock:
            forward, plan = [], []
            for c in cmds:
                name = c["command"]
                if name in self.mapping:
                    real, mult = self.mapping[name]
                    try:
                        n = int(c.get("count", 1))
                    except (TypeError, ValueError):
                        n = 0
                    if not 1 <= n <= 10:
                        plan.append(("error", name, "count must be an integer from 1 to 10"))
                    elif real is None:
                        plan.append(("noop", name, None))
                    else:
                        plan.append(("fwd", name, len(forward)))
                        forward.append({"command": real, "count": n * mult})
                elif name in ("pause", "resume", "step", "reset"):
                    plan.append(("fwd", name, len(forward)))
                    forward.append({k: v for k, v in c.items() if k in ("command", "seconds")})
                else:
                    plan.append(("error", name, "unknown command '%s'. Valid: T01-%s, reset, pause, resume, step" % (name, BLACKBOX_NAMES[-1])))
            self.commands += len(cmds)

            # Run in order; a reset banks the attempt's time first.
            done, chunk = [], []

            def flush():
                if chunk:
                    done.extend(self.bridge.run(chunk))
                    chunk.clear()

            for c in forward:
                if c["command"] == "reset":
                    flush()
                    self.banked_s += self.attempt_s
                    self.attempt_s = 0.0
                    self.attempts += 1
                    self.resetting = True
                    try:
                        res = self.bridge.run([c])
                        done.extend(res)
                        # The attempt clock starts now, even if the page's own reset timed out.
                        self.attempt_t0 = float((res[0].get("state") or {}).get("time_s") or 0)
                    finally:
                        self.resetting = False
                        self.started = True
                else:
                    chunk.append(c)
            flush()

            real_names = {real: name for name, (real, _) in self.mapping.items() if real}
            results, last_state = [], self.bridge.state
            for kind, name, ref in plan:
                if kind == "fwd":
                    r = done[ref]
                    last_state = r.get("state") or last_state
                    err = r.get("error")
                    if err:
                        for real, opaque in real_names.items():
                            err = err.replace("'%s'" % real, "'%s'" % opaque)
                        results.append({"ok": False, "command": name, "error": err, "state": self.agent_state(last_state)})
                    else:
                        results.append({"ok": True, "command": name, "state": self.agent_state(last_state)})
                elif kind == "noop":
                    results.append({"ok": True, "command": name, "state": self.agent_state(last_state)})
                else:
                    results.append({"ok": False, "command": name, "error": ref, "state": self.agent_state(last_state)})
            self.observe(last_state)
            results[-1]["state"] = self.agent_state(last_state)  # include a docking this request caused
            return results[0] if single else results


# ---------------------------------------------------------------------------
# Intermediate: Autopilot (bring your own controller)
#
# Real time: the sim runs on its own clock in the participant's tab. The participant's program sends
# requests (POST /ap/<id>/api/request) whenever it likes: "telemetry" returns the newest state,
# "control" fires thrusters right away. Their program is in the loop; the loop doesn't wait for it.
# The kit's clients (hackathon.client, hackathon_client.h/.hpp) wrap this endpoint.
# ---------------------------------------------------------------------------

MAX_FLIGHT_S = 600  # a flight that hasn't docked after this much sim time fails
TAB_LOST_S = 30  # a flight whose simulator tab has been gone this long is abandoned
CONNECTED_WINDOW_S = 3  # an autopilot that sent a request this recently counts as connected
CLIENT_TTL_S = 600  # forget an autopilot's per-client bookkeeping after this long without requests
MAX_PULSES = 50  # per axis per control request
AUTOPILOT_STATE_KEYS = ("status", "message", "time_s", "position", "velocity", "range", "range_rate", "attitude",
                        "rotation_rate", "rotation_rate_commanded")
# For each control input axis (x, y, z, roll, pitch, yaw): the thruster that increases that velocity
# (or rotation rate), and the one that decreases it.
INPUT_THRUSTERS = (("translate_backward", "translate_forward"), ("translate_right", "translate_left"), ("translate_up", "translate_down"),
                   ("roll_left", "roll_right"), ("pitch_up", "pitch_down"), ("yaw_left", "yaw_right"))
REQUEST_HELP = ('send {"request": "telemetry"}, {"request": "control", "inputs": [x, y, z, roll, pitch, yaw]}, '
                '{"request": "launch"} or {"request": "abort"}')


class RequestError(Exception):
    """A request that can't be served; it goes back as {"ok": false, "error": ...}."""


def to_pulses(inputs):
    """Six numbers -> six whole pulse counts, each rounded half away from zero and limited to +/-50."""
    if not isinstance(inputs, list) or len(inputs) != 6:
        raise RequestError('"inputs" must be a list of 6 numbers: [x, y, z, roll, pitch, yaw]')
    out = []
    for v in inputs:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise RequestError('"inputs" must be 6 finite numbers: [x, y, z, roll, pitch, yaw]')
        n = int(math.copysign(math.floor(abs(v) + 0.5), v))
        out.append(max(-MAX_PULSES, min(MAX_PULSES, n)))
    return out


def state_vector(s):
    """Flight state -> [x, y, z, x_dot, y_dot, z_dot, roll, pitch, yaw, roll_dot, pitch_dot, yaw_dot]."""
    try:
        p, v, a, r = s["position"], s["velocity"], s["attitude"], s["rotation_rate"]
        return [p["x"], p["y"], p["z"], v["x"], v["y"], v["z"], a["roll"], a["pitch"], a["yaw"], r["roll"], r["pitch"], r["yaw"]]
    except (KeyError, TypeError):
        return None


def commanded_rates(s):
    try:
        c = s["rotation_rate_commanded"]
        return [c["roll"], c["pitch"], c["yaw"]]
    except (KeyError, TypeError):
        return None


def time_ms(s):
    return int(round(float(s["time_s"]) * 1000)) if s and s.get("time_s") is not None else 0


class AutopilotLink(RemoteSession):
    def __init__(self, store, callsign):
        super().__init__(store, callsign)
        self.flight_lock = threading.RLock()
        self.flight = None  # the current or most recent flight
        self.history = []  # finished flights, newest last (the last 20)
        self.finished = 0  # total finished flights
        self.clients = {}  # per running autopilot (the id its client library makes up): what it has been shown
        self.last_request = 0.0  # last request from any autopilot
        self.last_tab = time.time()  # last sync from the simulator tab

    # ---- helpers ----

    def autopilot_connected(self):
        return time.time() - self.last_request < CONNECTED_WINDOW_S

    def flight_state(self, state, f=None):
        f = f or self.flight
        s = {k: v for k, v in (state or {}).items() if k in AUTOPILOT_STATE_KEYS}
        if "time_s" in s and f:
            s["time_s"] = round(max(0.0, s["time_s"] - f["t0"]), 3)
        return s

    def public_flight(self, f=None):
        f = f or self.flight
        if not f:
            return None
        return {"flight_id": f["id"], "status": f["status"], "commands": f["commands"], "result": f["result"]}

    def status(self):
        s = self.bridge.state or {}
        t = None
        if self.flight and self.flight["status"] == "flying" and "time_s" in s:
            t = round(max(0.0, s["time_s"] - self.flight["t0"]), 2)
        return {"callsign": self.callsign, "connected": self.bridge.connected(), "autopilot_connected": self.autopilot_connected(),
                "flight": self.public_flight(), "flight_time_s": t, "history": self.history[-10:], "finished": self.finished}

    # ---- lifecycle ----

    def launch(self):
        """Start a flight: reset the sim in the tab and wait for the Dragon to arrive. Returns immediately."""
        self.require_tab()
        with self.flight_lock:
            if self.flight and self.flight["status"] in ("launching", "flying"):
                raise HttpError(409, "a flight is already in progress")
            self.flight = {"id": secrets.token_hex(6), "status": "launching", "t0": 0.0, "commands": 0, "result": None, "final_state": None}
            flight = self.flight
        threading.Thread(target=self._prepare, args=(flight,), daemon=True).start()
        return self.public_flight(flight)

    def _prepare(self, flight):
        try:
            self.bridge.run([{"command": "reset"}])
        except TimeoutError:
            pass  # a slow arrival; keep waiting below
        deadline = time.time() + 300  # ~10 s on a normal laptop; generous for weak graphics
        last_contact = time.time()
        while (self.bridge.state or {}).get("status") != "flying":
            if self.bridge.connected():
                last_contact = time.time()
            # The arrival animation can freeze a weak tab for a few seconds; only give up on a long silence.
            if time.time() > deadline or time.time() - last_contact > 30:
                return self._finish(flight, "abort", "The Dragon never arrived at the start point. Is the simulator tab visible?", record=False)
            time.sleep(0.1)
        try:
            # Unpause in case anything left the sim paused; autopilots always fly with fine pulses.
            res = self.bridge.run([{"command": "translation_fine"}, {"command": "rotation_fine"}, {"command": "resume"}])
        except TimeoutError:
            return self._finish(flight, "abort", NOT_CONNECTED, record=False)
        with self.flight_lock:
            if flight["status"] != "launching":
                return
            flight["t0"] = float((res[-1].get("state") or {}).get("time_s") or 0)  # the flight clock starts now
            flight["status"] = "flying"

    def _finish(self, flight, outcome, message, time_s=0.0, record=True, state=None):
        with self.flight_lock:
            if flight["status"] == "done":
                return
            flight["status"] = "done"
            result = {"outcome": outcome, "time_s": round(time_s, 1), "message": message, "rank": None}
            if record and outcome in ("success", "fail"):
                self.store.add_run(tier="intermediate", callsign=self.callsign, model="", outcome=outcome,
                                   time_s=round(time_s, 1), commands=flight["commands"], message=message[:200])
                if outcome == "success":
                    result["rank"] = self.store.rank("intermediate", time_s)
            flight["result"] = result
            if flight["t0"] or state:
                flight["final_state"] = self.flight_state(state or self.bridge.state, flight)
            self.history.append(dict(result, flight_id=flight["id"]))
            del self.history[:-20]
            self.finished += 1

    def observe(self, state):
        """Every browser sync (~30 ms): catch the end of the flight."""
        self.last_tab = time.time()
        f = self.flight
        if not f or f["status"] != "flying" or not state or "time_s" not in state:
            return
        elapsed = max(0.0, float(state["time_s"]) - f["t0"])
        status = state.get("status")
        if status == "success":
            return self._finish(f, "success", "Docking successful.", elapsed, state=state)
        if status == "fail":
            return self._finish(f, "fail", state.get("message") or "Attempt failed.", elapsed, state=state)
        if elapsed > MAX_FLIGHT_S:
            return self._finish(f, "fail", "Out of time: no docking after %d s of sim time." % MAX_FLIGHT_S, elapsed, state=state)

    def check_timeouts(self):
        """Called periodically: abandon a flight whose simulator tab went away; forget idle autopilots."""
        f = self.flight
        if f and f["status"] == "flying" and time.time() - self.last_tab > TAB_LOST_S:
            self._finish(f, "abort", "The simulator tab was closed or hidden for %d s." % TAB_LOST_S, record=False)
        with self.flight_lock:
            for k in [k for k, c in self.clients.items() if time.time() - c["last"] > CLIENT_TTL_S]:
                del self.clients[k]

    def _elapsed(self):
        s = self.bridge.state or {}
        return max(0.0, float(s.get("time_s") or 0) - (self.flight["t0"] if self.flight else 0))

    # ---- the participant's API: POST /ap/<id>/api/request ----

    def request(self, body):
        """One request from an autopilot; always returns a reply with "ok"."""
        self.last_request = time.time()
        try:
            if not isinstance(body, dict):
                raise RequestError("requests must be JSON objects; " + REQUEST_HELP)
            with self.flight_lock:
                key = str(body.get("client") or "")[:64]
                client = self.clients.get(key)
                if client is None:
                    # flight: the flight it has seen in progress; served/hold_ms: the telemetry it was last given,
                    # and when its last control inputs took effect.
                    client = self.clients[key] = {"flight": None, "served": None, "served_flight": None, "hold_ms": -1}
                client["last"] = time.time()
            kind = body.get("request")
            if kind == "telemetry":
                return self._telemetry(client)
            if kind == "control":
                return self._control(client, to_pulses(body.get("inputs")))
            if kind == "launch":
                try:
                    f = self.launch()
                except HttpError as e:
                    raise RequestError(str(e)) from None
                with self.flight_lock:
                    client["flight"] = f["flight_id"]
                return {"ok": True, "flight_id": f["flight_id"]}
            if kind == "abort":
                return self._abort(str(body.get("reason") or "")[:200])
            raise RequestError("unknown request; " + REQUEST_HELP)
        except RequestError as e:
            return {"ok": False, "error": str(e)}

    def _telemetry(self, client):
        reply = {"ok": True, "status": "waiting", "docked": False, "flight_id": None, "timestamp_ms": 0,
                 "state": None, "commanded_rates": None, "rank": None, "message": ""}
        with self.flight_lock:
            f = self.flight
            # A finished flight belongs to the autopilot that flew it: one that starts later sees "waiting".
            if f is None or (f["status"] == "done" and client["flight"] != f["id"]):
                if not self.bridge.connected():
                    reply.update(status="offline", message=NOT_CONNECTED)
                else:
                    reply["message"] = "No flight yet: press LAUNCH in the simulator tab, or send a launch request."
                return reply
            reply["flight_id"] = f["id"]
            if f["status"] == "launching":
                client["flight"] = f["id"]
                reply.update(status="launching", message="Launching: the Dragon takes a few seconds to arrive.")
                return reply
            if client["served_flight"] != f["id"]:
                client.update(served=None, served_flight=f["id"], hold_ms=-1)
            if f["status"] == "flying":
                client["flight"] = f["id"]
                latest = self.flight_state(self.bridge.state, f)
                # Newest telemetry, but never from before this autopilot's last inputs took effect:
                # telemetry that doesn't show them yet would make it fire the same burn twice.
                if "position" in latest and time_ms(latest) > client["hold_ms"]:
                    client["served"] = latest
                s = client["served"]
                reply["status"] = "flying"
                if s:
                    reply.update(timestamp_ms=time_ms(s), state=state_vector(s), commanded_rates=commanded_rates(s))
                return reply
            # Done. Always report a new timestamp, so an autopilot that skips repeated timestamps sees the ending.
            r, s = f["result"], f["final_state"] or client["served"]
            reply.update(status="docked" if r["outcome"] == "success" else "failed", docked=r["outcome"] == "success",
                         rank=r["rank"], message=r["message"] or "")
            if s and "position" in s:
                reply.update(timestamp_ms=max(time_ms(s), time_ms(client["served"]) + 1), state=state_vector(s), commanded_rates=commanded_rates(s))
            return reply

    def _control(self, client, pulses):
        f = self.flight
        if not f or f["status"] != "flying":
            raise RequestError("no flight in progress (status: %s)" % ("waiting" if not f else "launching" if f["status"] == "launching" else "ended"))
        commands = [{"command": INPUT_THRUSTERS[i][0 if n > 0 else 1], "count": abs(n)} for i, n in enumerate(pulses) if n]
        if not commands:
            return {"ok": True, "timestamp_ms": time_ms(self.flight_state(self.bridge.state, f)), "pulses": pulses}
        if not self.bridge.connected():
            raise RequestError(NOT_CONNECTED)
        with self.lock:
            try:
                results = self.bridge.run(commands)
            except TimeoutError:
                raise RequestError(NOT_CONNECTED) from None
            f["commands"] += len(commands)
        applied_ms = time_ms(self.flight_state((results[-1].get("state") if results else None) or self.bridge.state, f))
        with self.flight_lock:
            client["hold_ms"] = max(client["hold_ms"], applied_ms)
        errors = [r.get("error") for r in results if not r.get("ok")]
        if errors:
            return {"ok": False, "error": "; ".join(errors), "timestamp_ms": applied_ms, "pulses": pulses}
        return {"ok": True, "timestamp_ms": applied_ms, "pulses": pulses}

    def _abort(self, reason):
        f = self.flight
        if not f or f["status"] != "flying":
            raise RequestError("no flight in progress")
        self._finish(f, "fail", reason or "Aborted by the autopilot.", self._elapsed())
        return {"ok": True}

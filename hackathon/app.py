#!/usr/bin/env python3
"""Hosted hackathon server: the docking sim behind a password, with AI CAPCOM, an AI pilot,
the Black Box challenge, and a leaderboard for the booth TV.

    ANTHROPIC_API_KEY=sk-ant-... HACKATHON_PASSWORD=letmein python hackathon/app.py

Environment variables (see hackathon/README.md):
    ANTHROPIC_API_KEY     your Anthropic API key (required for the AI features)
    HACKATHON_PASSWORD    password participants type to get in (required)
    ADMIN_PASSWORD        password for /admin, where staff can delete leaderboard entries
    SESSION_SECRET        any long random string; keeps people logged in across restarts
    DAILY_BUDGET_USD      stop calling Claude once today's spend reaches this (default 50)
    DATA_FILE             where to keep the leaderboard and memory (default hackathon/data/hackathon.json)
    LEADERBOARD_PUBLIC    "1" lets anyone view /leaderboard without logging in (default 0)
    PORT                  port to listen on (default 8000)
"""

import hashlib
import hmac
import json
import mimetypes
import os
import random
import re
import secrets
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from server import Bridge  # noqa: E402  (the same relay the local server.py uses)

from claude import MODELS, THRUSTERS, BudgetExceeded, Claude  # noqa: E402
from store import Store  # noqa: E402

SITE_DIR = os.path.join(os.path.dirname(HERE), "iss-sim.spacex.com")
PAGES_DIR = os.path.join(HERE, "pages")

PASSWORD = os.environ.get("HACKATHON_PASSWORD", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
SECRET = (os.environ.get("SESSION_SECRET") or secrets.token_hex(32)).encode()
LEADERBOARD_PUBLIC = os.environ.get("LEADERBOARD_PUBLIC") == "1"

PILOT_MESSAGE_BUDGET = 10  # radio calls a human CAPCOM gets per AI-pilot flight
CAPCOM_MIN_INTERVAL_S = 2.0  # per run, between CAPCOM calls
SESSION_TTL_S = 3 * 3600  # forget in-memory runs and missions after this long
BLACKBOX_DEAD_THRUSTERS = 2

store = Store(os.environ.get("DATA_FILE") or os.path.join(HERE, "data", "hackathon.json"))
claude = Claude(store, float(os.environ.get("DAILY_BUDGET_USD") or 50))

mimetypes.add_type("model/gltf-binary", ".glb")
mimetypes.add_type("model/gltf+json", ".gltf")
mimetypes.add_type("application/javascript", ".js")


def token(role):
    return hmac.new(SECRET, role.encode(), hashlib.sha256).hexdigest()


def clean_callsign(s):
    s = re.sub(r"[^\w .'\-]", "", str(s or ""), flags=re.UNICODE).strip()
    return s[:24]


class HttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# In-memory sessions (a server restart forgets runs in progress; finished runs are in the store)
# ---------------------------------------------------------------------------

lock = threading.Lock()
beginner_runs = {}
pilot_runs = {}
missions = {}


def _expire():
    cutoff = time.time() - SESSION_TTL_S
    with lock:
        for table in (beginner_runs, pilot_runs, missions):
            for k in [k for k, v in table.items() if v["created"] < cutoff]:
                del table[k]


def _get(table, run_id, what="run"):
    with lock:
        run = table.get(str(run_id))
    if run is None:
        raise HttpError(404, "unknown or expired %s; start a new one" % what)
    return run


# ---------------------------------------------------------------------------
# Beginner: you fly, AI CAPCOM talks you in
# ---------------------------------------------------------------------------


def beginner_start(body):
    callsign = clean_callsign(body.get("callsign"))
    if not callsign:
        raise HttpError(400, "pick a callsign first")
    model = body.get("model") or "none"
    if model != "none" and model not in MODELS:
        raise HttpError(400, "unknown model")
    _expire()
    run = {"id": secrets.token_hex(8), "created": time.time(), "callsign": callsign, "model": model,
           "transcript": [], "last_call": 0.0, "finished": False}
    with lock:
        beginner_runs[run["id"]] = run
    reply = ""
    if model != "none":
        history = store.profile(callsign).get("capcom_history", [])
        reply = _capcom(run, "start", body.get("telemetry") or {}, [], None, history)
    return {"run_id": run["id"], "reply": reply}


def _capcom(run, kind, telemetry, events, crew_message, history=None):
    if history is None:
        history = store.profile(run["callsign"]).get("capcom_history", [])
    if crew_message:
        run["transcript"].append("%s: %s" % (run["callsign"], crew_message))
    reply = claude.capcom(run["model"], run["callsign"], history, run["transcript"], telemetry, events, crew_message, kind)
    if reply:
        run["transcript"].append("CAPCOM: " + reply)
    return reply


def beginner_capcom(body):
    run = _get(beginner_runs, body.get("run_id"))
    if run["model"] == "none" or run["finished"]:
        return {"reply": ""}
    now = time.time()
    if now - run["last_call"] < CAPCOM_MIN_INTERVAL_S and not body.get("crew_message"):
        return {"reply": ""}
    run["last_call"] = now
    crew = str(body.get("crew_message") or "")[:300] or None
    events = [str(e)[:200] for e in (body.get("events") or [])][:8]
    return {"reply": _capcom(run, "flight", body.get("telemetry") or {}, events, crew)}


def beginner_finish(body):
    run = _get(beginner_runs, body.get("run_id"))
    if run["finished"]:
        raise HttpError(409, "run already finished")
    run["finished"] = True
    outcome = body.get("outcome")
    if outcome not in ("success", "fail", "abort"):
        raise HttpError(400, "bad outcome")
    time_s = float(body.get("time_s") or 0)
    if outcome == "success" and not (5 <= time_s <= 7200):
        raise HttpError(400, "implausible time")
    stats = body.get("stats") or {}
    summary = "%s %s" % (str(body.get("message") or "")[:160], str(stats.get("summary") or "")[:300])
    rank = None
    if outcome != "abort":
        store.add_run(tier="beginner", callsign=run["callsign"], model=run["model"], outcome=outcome,
                      time_s=round(time_s, 1), message=str(body.get("message") or "")[:200])
        if outcome == "success":
            rank = store.rank("beginner", time_s)
    debrief = ""
    if run["model"] != "none" and outcome != "abort":
        try:
            debrief = _capcom(run, "debrief", body.get("telemetry") or {}, ["Attempt ended: %s. %s" % (outcome, summary)], None)
        except Exception as e:  # the run is recorded even if the debrief fails
            debrief = ""
            print("debrief failed:", e)
    if outcome != "abort":
        def remember(p):
            h = p.setdefault("capcom_history", [])
            h.append({"outcome": outcome, "time_s": round(time_s, 1), "model": run["model"], "summary": summary.strip(), "debrief": debrief})
            del h[:-10]
        store.update_profile(run["callsign"], remember)
    return {"debrief": debrief, "rank": rank}


# ---------------------------------------------------------------------------
# Intermediate: the AI flies, you are its CAPCOM
# ---------------------------------------------------------------------------


def pilot_start(body):
    callsign = clean_callsign(body.get("callsign"))
    if not callsign:
        raise HttpError(400, "pick a callsign first")
    model = body.get("model")
    if model not in MODELS:
        raise HttpError(400, "pick a pilot model")
    orders = str(body.get("standing_orders") or "")[:800]
    store.update_profile(callsign, lambda p: p.__setitem__("standing_orders", orders))
    _expire()
    run = {"id": secrets.token_hex(8), "created": time.time(), "callsign": callsign, "model": model, "orders": orders,
           "capcom": [], "delivered": 0, "log": [], "messages_left": PILOT_MESSAGE_BUDGET, "max_time": 0.0, "turns": 0,
           "finished": False, "busy": False}
    with lock:
        pilot_runs[run["id"]] = run
    return {"run_id": run["id"], "messages_left": run["messages_left"]}


def _pilot_state(state):
    keep = ("time_s", "position", "velocity", "range", "range_rate", "attitude", "rotation_rate", "rotation_rate_commanded")
    return {k: state[k] for k in keep if k in state}


def pilot_turn(body):
    run = _get(pilot_runs, body.get("run_id"))
    if run["finished"]:
        raise HttpError(409, "flight is over")
    state = _pilot_state(body.get("state") or {})
    t = float(state.get("time_s") or 0)
    if t + 0.5 < run["max_time"]:
        raise HttpError(400, "sim time went backwards")
    if run["busy"]:
        raise HttpError(429, "previous turn still in progress")
    run["busy"] = True
    seen = run["delivered"]
    calls = [dict(m, new=i >= seen) for i, m in enumerate(run["capcom"])]
    try:
        decision = claude.pilot_turn(run["model"], run["orders"], calls, run["log"], state)
        run["delivered"] = len(calls)
    finally:
        run["busy"] = False
    commands = []
    for c in decision.get("commands") or []:
        if c.get("command") in THRUSTERS:
            commands.append({"command": c["command"], "count": max(1, min(20, int(c.get("count") or 1)))})
    coast = max(0.5, min(10.0, float(decision.get("coast_seconds") or 2)))
    radio = str(decision.get("radio") or "")[:200]
    run["max_time"] = t
    run["turns"] += 1
    p, att = state.get("position", {}), state.get("attitude", {})
    run["log"].append("T+%.1fs pos=(%.1f,%.1f,%.1f) att=(%.1f,%.1f,%.1f) -> fired %s, coast %.1fs%s" % (
        t, p.get("x", 0), p.get("y", 0), p.get("z", 0), att.get("roll", 0), att.get("pitch", 0), att.get("yaw", 0),
        ", ".join("%s x%d" % (c["command"], c["count"]) for c in commands) or "nothing", coast,
        (' radio "%s"' % radio) if radio else ""))
    del run["log"][:-12]
    return {"commands": commands, "coast_seconds": coast, "radio": radio}


def pilot_message(body):
    run = _get(pilot_runs, body.get("run_id"))
    if run["finished"]:
        raise HttpError(409, "flight is over")
    text = str(body.get("text") or "").strip()[:300]
    if not text:
        raise HttpError(400, "empty message")
    with lock:
        if run["messages_left"] <= 0:
            raise HttpError(429, "no radio calls left this flight")
        run["messages_left"] -= 1
        run["capcom"].append({"t": run["max_time"], "text": text})
    return {"messages_left": run["messages_left"]}


def pilot_finish(body):
    run = _get(pilot_runs, body.get("run_id"))
    if run["finished"]:
        raise HttpError(409, "flight already finished")
    run["finished"] = True
    outcome = body.get("outcome")
    if outcome not in ("success", "fail", "abort"):
        raise HttpError(400, "bad outcome")
    time_s = float(body.get("time_s") or 0)
    message = str(body.get("message") or "")[:200]
    # The server watched every turn, so it can sanity-check the reported time.
    if outcome == "success" and not (run["turns"] >= 3 and run["max_time"] - 0.5 <= time_s <= run["max_time"] + 10.5):
        raise HttpError(400, "reported time doesn't match the flight log")
    used = PILOT_MESSAGE_BUDGET - run["messages_left"]
    rank = None
    if outcome != "abort":
        store.add_run(tier="intermediate", callsign=run["callsign"], model=run["model"], outcome=outcome,
                      time_s=round(time_s, 1), messages_used=used, message=message)
        if outcome == "success":
            rank = store.rank("intermediate", time_s)
    debrief = ""
    if outcome != "abort":
        try:
            debrief = claude.pilot_debrief(run["model"], run["orders"], run["capcom"], run["log"], outcome, message, time_s)
        except Exception as e:
            print("pilot debrief failed:", e)
        store.update_profile(run["callsign"], lambda p: p.setdefault("pilot_history", []).append(
            {"outcome": outcome, "time_s": round(time_s, 1), "model": run["model"], "messages_used": used, "debrief": debrief}))
    return {"debrief": debrief, "rank": rank, "messages_used": used}


# ---------------------------------------------------------------------------
# Advanced: Black Box Dragon. The agent gets opaque thrusters T01..T14 and must work out
# what each does. The mapping never leaves this server; the browser only sees real commands.
# ---------------------------------------------------------------------------


def new_mission(body):
    callsign = clean_callsign(body.get("callsign"))
    if not callsign:
        raise HttpError(400, "pick a team callsign first")
    _expire()
    names = ["T%02d" % i for i in range(1, len(THRUSTERS) + BLACKBOX_DEAD_THRUSTERS + 1)]
    rng = random.SystemRandom()
    rng.shuffle(names)
    mapping = {}
    for name, real in zip(names, THRUSTERS + [None] * BLACKBOX_DEAD_THRUSTERS):
        mapping[name] = (real, rng.choice((1, 2, 3)))
    m = {"id": secrets.token_urlsafe(12), "created": time.time(), "callsign": callsign, "mapping": mapping,
         "bridge": Bridge(), "banked_s": 0.0, "attempt_s": 0.0, "attempts": 0, "commands": 0, "pulses": 0,
         "started": False, "resetting": False, "complete": False, "result": None, "lock": threading.Lock()}
    with lock:
        missions[m["id"]] = m
    return {"mission_id": m["id"]}


def mission_status(m):
    s = m["bridge"].state or {}
    return {"callsign": m["callsign"], "connected": m["bridge"].connected(), "attempts": m["attempts"],
            "mission_time_s": round(m["banked_s"] + m["attempt_s"], 2), "status": s.get("status"), "commands": m["commands"],
            "complete": m["complete"], "result": m["result"]}


def mission_observe(m, state):
    """Called on every browser sync: keep the mission clock and catch the docking."""
    if not state or not m["started"] or m["resetting"] or m["complete"]:
        return
    if state.get("status") in ("flying", "success", "fail") and "time_s" in state:
        m["attempt_s"] = float(state["time_s"])
    if state.get("status") == "success":
        m["complete"] = True
        total = round(m["banked_s"] + m["attempt_s"], 1)
        store.add_run(tier="advanced", callsign=m["callsign"], model="", outcome="success", time_s=total,
                      attempts=m["attempts"], commands=m["commands"], message="")
        m["result"] = {"time_s": total, "rank": store.rank("advanced", total)}


# Black Box agents get "sensor" readings only: where they are and how they're pointed. No
# velocities or rates, which would reveal a thruster's effect the instant it fires (so it could be
# identified while paused, at no cost). Effects show up only as time passes and must be estimated.
AGENT_STATE_KEYS = ("status", "message", "paused", "time_s", "position", "range", "attitude")


def _agent_state(m, state):
    s = {k: v for k, v in (state or {}).items() if k in AGENT_STATE_KEYS}
    s["mission_time_s"] = round(m["banked_s"] + m["attempt_s"], 3)
    s["mission_complete"] = m["complete"]
    return s


AGENT_COMMANDS = {
    "T01 ... T%02d" % (len(THRUSTERS) + BLACKBOX_DEAD_THRUSTERS): "Thrusters. What each does is unknown. Optional 'count' (1-10).",
    "reset": "Start a new attempt (the mission clock keeps running across attempts).",
    "pause": "Freeze simulation time.",
    "resume": "Unfreeze simulation time.",
    "step": "While paused, advance sim time by 'seconds' (default 1, max 60).",
}


def mission_command(m, body):
    single = isinstance(body, dict)
    cmds = [body] if single else body
    if not isinstance(cmds, list) or not cmds or len(cmds) > 50 or not all(isinstance(c, dict) and isinstance(c.get("command"), str) for c in cmds):
        raise HttpError(400, 'body must be {"command": "<name>", ...} or a list of those')
    if not m["bridge"].connected():
        raise HttpError(503, "The simulator tab for this mission isn't connected. Open the Black Box page in a browser and keep it visible.")
    with m["lock"]:  # one agent request at a time per mission keeps the clock honest
        forward, plan = [], []
        for c in cmds:
            name = c["command"]
            if name in m["mapping"]:
                real, mult = m["mapping"][name]
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
                    m["pulses"] += n
            elif name in ("pause", "resume", "step", "reset"):
                plan.append(("fwd", name, len(forward)))
                forward.append({k: v for k, v in c.items() if k in ("command", "seconds")})
            else:
                plan.append(("error", name, "unknown command '%s'. Valid: T01-T%02d, reset, pause, resume, step" % (name, len(THRUSTERS) + BLACKBOX_DEAD_THRUSTERS)))
        m["commands"] += len(cmds)

        # Run in order; a reset banks the attempt's time first.
        results, done = [], []
        chunk = []

        def flush():
            if chunk:
                done.extend(m["bridge"].run(chunk))
                chunk.clear()

        for c in forward:
            if c["command"] == "reset":
                flush()
                m["banked_s"] += m["attempt_s"]
                m["attempt_s"] = 0.0
                m["attempts"] += 1
                m["resetting"] = True
                try:
                    done.extend(m["bridge"].run([c]))
                finally:
                    m["resetting"] = False
                    m["started"] = True
            else:
                chunk.append(c)
        flush()

        real_names = {real: name for name, (real, _) in m["mapping"].items() if real}
        last_state = m["bridge"].state
        for kind, name, ref in plan:
            if kind == "fwd":
                r = done[ref]
                last_state = r.get("state") or last_state
                err = r.get("error")
                if err:
                    for real, opaque in real_names.items():
                        err = err.replace("'%s'" % real, "'%s'" % opaque)
                    results.append({"ok": False, "command": name, "error": err, "state": _agent_state(m, last_state)})
                else:
                    results.append({"ok": True, "command": name, "state": _agent_state(m, last_state)})
            elif kind == "noop":
                results.append({"ok": True, "command": name, "state": _agent_state(m, last_state)})
            else:
                results.append({"ok": False, "command": name, "error": ref, "state": _agent_state(m, last_state)})
        mission_observe(m, last_state)
        if results:
            results[-1]["state"] = _agent_state(m, last_state)  # include a docking this request caused
        return results[0] if single else results


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

LOGIN_ATTEMPTS = {}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ISSHackathon/1.0"

    def log_message(self, fmt, *args):
        if "/bridge/sync" not in self.path and "/api/beginner/capcom" not in self.path:
            sys.stderr.write("%s %s\n" % (self.client_ip(), fmt % args))

    def client_ip(self):
        return (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()

    def cookies(self):
        out = {}
        for part in (self.headers.get("Cookie") or "").split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                out[k] = v
        return out

    def authed(self, role="participant"):
        c = self.cookies().get("iss_" + role, "")
        return hmac.compare_digest(c, token(role))

    def send(self, status, body, ctype="application/json", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def redirect(self, location, headers=None):
        h = {"Location": location}
        h.update(headers or {})
        self.send(303, b"", "text/plain", h)

    def read_body(self):
        return self._body

    def read_json(self):
        try:
            return json.loads(self.read_body() or b"null")
        except ValueError:
            raise HttpError(400, "request body must be JSON")

    def serve_file(self, path):
        if not os.path.isfile(path):
            return self.send(404, {"ok": False, "error": "not found"})
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # Models and textures are big and never change during the event.
        self.send_header("Cache-Control", "max-age=3600" if not path.endswith((".html", ".js", ".css")) else "no-cache")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def cookie_header(self, name, value, max_age):
        secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
        return "%s=%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Lax%s" % (name, value, max_age, secure)

    # ---- routing ----

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        try:
            self.route_get()
        except HttpError as e:
            self.send(e.status, {"ok": False, "error": str(e)})

    def do_POST(self):
        # Always consume the body, even for requests we reject, or a keep-alive connection
        # would read the leftover bytes as the next request.
        n = int(self.headers.get("Content-Length") or 0)
        if n > 256 * 1024:
            self.close_connection = True
            return self.send(413, {"ok": False, "error": "request too large"})
        self._body = self.rfile.read(n) if n else b""
        try:
            self.route_post()
        except HttpError as e:
            self.send(e.status, {"ok": False, "error": str(e)})
        except BudgetExceeded as e:
            self.send(402, {"ok": False, "error": str(e)})
        except TimeoutError as e:
            self.send(504, {"ok": False, "error": str(e)})
        except Exception as e:  # don't leak stack traces, but do log them
            import traceback
            traceback.print_exc()
            self.send(500, {"ok": False, "error": "server error: %s" % type(e).__name__})

    def route_get(self):
        url = urllib.parse.urlsplit(self.path)
        path = url.path

        if path == "/healthz":
            return self.send(200, {"ok": True})
        if path == "/login":
            return self.serve_file(os.path.join(PAGES_DIR, "login.html"))
        if path.startswith("/bb/"):
            return self.blackbox_get(path)
        if path in ("/leaderboard", "/api/leaderboard") and LEADERBOARD_PUBLIC:
            pass
        elif not self.authed():
            if path.startswith(("/api/", "/hackathon/")):
                raise HttpError(401, "not logged in")
            return self.redirect("/login?next=" + urllib.parse.quote(self.path))

        if path == "/":
            return self.serve_file(os.path.join(PAGES_DIR, "hub.html"))
        if path == "/leaderboard":
            return self.serve_file(os.path.join(PAGES_DIR, "leaderboard.html"))
        if path == "/admin":
            if not self.authed("admin"):
                return self.redirect("/login?admin=1&next=/admin")
            return self.serve_file(os.path.join(PAGES_DIR, "admin.html"))
        if path == "/sim":
            return self.redirect("/sim/" + ("?" + url.query if url.query else ""))
        if path.startswith("/sim/"):
            rel = urllib.parse.unquote(path[len("/sim/"):]) or "index.html"
            full = os.path.normpath(os.path.join(SITE_DIR, rel))
            if not full.startswith(SITE_DIR + os.sep):
                raise HttpError(404, "not found")
            return self.serve_file(full)
        if path == "/hackathon/config":
            return self.send(200, {
                "models": [{"id": k, "label": v["label"]} for k, v in MODELS.items()],
                "pilot_message_budget": PILOT_MESSAGE_BUDGET,
                "ai_available": claude.client is not None,
            })
        if path == "/api/leaderboard":
            boards = store.leaderboard()
            for runs in boards.values():
                for r in runs:
                    r["model_label"] = MODELS.get(r.get("model"), {}).get("label", "Solo" if r.get("model") == "none" else "")
            return self.send(200, boards)
        if path.startswith("/api/blackbox/mission/"):
            m = _get(missions, path.rsplit("/", 1)[1], "mission")
            return self.send(200, mission_status(m))
        if path == "/api/profile":
            q = urllib.parse.parse_qs(url.query)
            p = store.profile(clean_callsign((q.get("callsign") or [""])[0]))
            return self.send(200, {"standing_orders": p.get("standing_orders", ""), "capcom_history": p.get("capcom_history", [])[-5:],
                                   "pilot_history": p.get("pilot_history", [])[-5:]})
        if path == "/api/admin/runs":
            if not self.authed("admin"):
                raise HttpError(401, "admin only")
            return self.send(200, sorted(store.runs(), key=lambda r: -r["created"]))
        raise HttpError(404, "not found")

    def route_post(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/login":
            return self.login()
        if path == "/logout":
            return self.redirect("/login", {"Set-Cookie": self.cookie_header("iss_participant", "", 0)})
        if path.startswith("/bb/"):
            return self.blackbox_post(path)
        if not self.authed():
            raise HttpError(401, "not logged in")
        if path == "/sim/bridge/sync":
            raise HttpError(404, "no local bridge on the hosted server")

        routes = {
            "/api/beginner/start": beginner_start,
            "/api/beginner/capcom": beginner_capcom,
            "/api/beginner/finish": beginner_finish,
            "/api/pilot/start": pilot_start,
            "/api/pilot/turn": pilot_turn,
            "/api/pilot/message": pilot_message,
            "/api/pilot/finish": pilot_finish,
            "/api/blackbox/mission": new_mission,
        }
        if path in routes:
            body = self.read_json()
            if not isinstance(body, dict):
                raise HttpError(400, "body must be a JSON object")
            return self.send(200, routes[path](body))
        if path == "/api/admin/delete":
            if not self.authed("admin"):
                raise HttpError(401, "admin only")
            return self.send(200, {"ok": store.delete_run(str(self.read_json().get("id")))})
        raise HttpError(404, "not found")

    def login(self):
        ip = self.client_ip()
        now = time.time()
        recent = [t for t in LOGIN_ATTEMPTS.get(ip, []) if now - t < 60]
        if len(recent) >= 10:
            return self.send(429, "Too many attempts. Wait a minute.", "text/plain")
        LOGIN_ATTEMPTS[ip] = recent + [now]
        form = urllib.parse.parse_qs(self.read_body().decode(errors="replace"))
        password = (form.get("password") or [""])[0]
        nxt = (form.get("next") or ["/"])[0]
        if not nxt.startswith("/") or nxt.startswith("//"):
            nxt = "/"
        cookies = []
        if PASSWORD and hmac.compare_digest(password.encode(), PASSWORD.encode()):
            cookies.append(self.cookie_header("iss_participant", token("participant"), 14 * 86400))
        if ADMIN_PASSWORD and hmac.compare_digest(password.encode(), ADMIN_PASSWORD.encode()):
            # The admin password also works as a participant password.
            cookies.append(self.cookie_header("iss_participant", token("participant"), 14 * 86400))
            cookies.append(self.cookie_header("iss_admin", token("admin"), 86400))
        if not cookies:
            return self.redirect("/login?error=1&next=" + urllib.parse.quote(nxt))
        self.send_response(303)
        self.send_header("Location", nxt)
        for c in cookies:
            self.send_header("Set-Cookie", c)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---- Black Box: /bb/<mission_id>/... (the mission id is the credential) ----

    def _mission(self, path):
        parts = path.split("/")  # ['', 'bb', id, ...]
        if len(parts) < 3:
            raise HttpError(404, "not found")
        return _get(missions, parts[2], "mission"), "/" + "/".join(parts[3:])

    def blackbox_get(self, path):
        m, sub = self._mission(path)
        if sub.rstrip("/") in ("", "/api"):
            return self.send(200, {"endpoints": ["GET /api/state", "POST /api/command"], "commands": AGENT_COMMANDS,
                                   "docs": "See the Black Box briefing in hackathon/CHALLENGES.md"})
        if sub == "/api/state":
            if not m["bridge"].connected():
                raise HttpError(503, "The simulator tab for this mission isn't connected. Open the Black Box page in a browser and keep it visible.")
            return self.send(200, dict(_agent_state(m, m["bridge"].state), connected=True))
        raise HttpError(404, "not found")

    def blackbox_post(self, path):
        m, sub = self._mission(path)
        body = self.read_json()
        if sub == "/bridge/sync":
            if not self.authed():
                raise HttpError(401, "not logged in")
            state = (body or {}).get("state")
            commands = m["bridge"].sync(state, (body or {}).get("results", []))
            mission_observe(m, state)
            return self.send(200, {"commands": commands, "mission": mission_status(m)})
        if sub == "/api/command":
            return self.send(200, mission_command(m, body))
        raise HttpError(404, "not found")


def main():
    if not PASSWORD:
        sys.exit("Set HACKATHON_PASSWORD (the password participants will type).")
    if claude.client is None:
        print("WARNING: ANTHROPIC_API_KEY is not set; CAPCOM and the AI pilot won't work.")
    port = int(os.environ.get("PORT") or 8000)
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.daemon_threads = True
    print("Hackathon server on http://localhost:%d" % port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

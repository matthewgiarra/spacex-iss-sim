#!/usr/bin/env python3
"""Hosted hackathon server: the docking sim behind a password, with AI CAPCOM (beginner),
bring-your-own Autopilot (intermediate), Black Box Dragon (advanced), and a leaderboard for the booth TV.

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
import io
import json
import mimetypes
import os
import re
import secrets
import sys
import threading
import time
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from claude import MODELS, BudgetExceeded, Claude  # noqa: E402
from remote import AGENT_COMMANDS, REQUEST_HELP, AutopilotLink, BlackBoxMission, HttpError  # noqa: E402
from store import Store  # noqa: E402

SITE_DIR = os.path.join(os.path.dirname(HERE), "iss-sim.spacex.com")
PAGES_DIR = os.path.join(HERE, "pages")

PASSWORD = os.environ.get("HACKATHON_PASSWORD", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
SECRET = (os.environ.get("SESSION_SECRET") or secrets.token_hex(32)).encode()
LEADERBOARD_PUBLIC = os.environ.get("LEADERBOARD_PUBLIC") == "1"

CAPCOM_MIN_INTERVAL_S = 2.0  # per run, between CAPCOM calls
SESSION_TTL_S = 3 * 3600  # forget in-memory runs and remote sessions after this long

store = Store(os.environ.get("DATA_FILE") or os.path.join(HERE, "data", "hackathon.json"))
# The intermediate tier used to be "Role Reversal" (an AI pilot); its runs don't belong on the Autopilot board.
store.purge(lambda r: r.get("tier") == "intermediate" and "messages_used" in r)
claude = Claude(store, float(os.environ.get("DAILY_BUDGET_USD") or 50))

mimetypes.add_type("model/gltf-binary", ".glb")
mimetypes.add_type("model/gltf+json", ".gltf")
mimetypes.add_type("application/javascript", ".js")


def token(role):
    return hmac.new(SECRET, role.encode(), hashlib.sha256).hexdigest()


def clean_callsign(s):
    s = re.sub(r"[^\w .'\-]", "", str(s or ""), flags=re.UNICODE).strip()
    return s[:24]


# ---------------------------------------------------------------------------
# In-memory sessions (a server restart forgets runs in progress; finished runs are in the store)
# ---------------------------------------------------------------------------

lock = threading.Lock()
beginner_runs = {}
sessions = {"autopilot": {}, "blackbox": {}}  # remote.AutopilotLink / remote.BlackBoxMission by id


def _created(v):
    return v["created"] if isinstance(v, dict) else v.created


def _expire():
    cutoff = time.time() - SESSION_TTL_S
    with lock:
        for table in (beginner_runs, sessions["autopilot"], sessions["blackbox"]):
            for k in [k for k, v in table.items() if _created(v) < cutoff]:
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
# Intermediate (Autopilot) and Advanced (Black Box): a participant's own program flies the sim
# in their browser tab through this server. The session logic lives in remote.py.
# ---------------------------------------------------------------------------


def new_session(kind, body):
    callsign = clean_callsign(body.get("callsign"))
    if not callsign:
        raise HttpError(400, "pick a callsign first")
    _expire()
    s = (AutopilotLink if kind == "autopilot" else BlackBoxMission)(store, callsign)
    with lock:
        sessions[kind][s.id] = s
    return {"id": s.id, "mission_id": s.id}


def watchdog():
    """End autopilot flights whose program went silent, so a tab is never stuck mid-flight."""
    while True:
        time.sleep(2)
        with lock:
            links = list(sessions["autopilot"].values())
        for link in links:
            try:
                link.check_timeouts()
            except Exception as e:
                print("watchdog:", e)


_kit_zip = None
KIT_FILES = ("README.md", "autopilot.py", "hackathon/__init__.py", "hackathon/client.py", "include/hackathon_client.h",
             "include/hackathon_client.hpp", "examples/autopilot.c", "examples/autopilot.cpp")


def kit_zip():
    """The autopilot starter kit, zipped on first request."""
    global _kit_zip
    if _kit_zip is None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name in KIT_FILES:
                z.write(os.path.join(HERE, "kit", *name.split("/")), "dragon-autopilot-kit/" + name)
            z.write(os.path.join(HERE, "AUTOPILOT_API.md"), "dragon-autopilot-kit/AUTOPILOT_API.md")
        _kit_zip = buf.getvalue()
    return _kit_zip


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

LOGIN_ATTEMPTS = {}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ISSHackathon/1.0"

    def log_message(self, fmt, *args):
        if not any(p in self.path for p in ("/bridge/sync", "/api/beginner/capcom", "/api/command")):
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
        if path == "/kit.zip":
            return self.send(200, kit_zip(), "application/zip", {"Content-Disposition": 'attachment; filename="dragon-autopilot-kit.zip"'})
        if path.startswith(("/bb/", "/ap/")):
            return self.remote_get(path, url.query)
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
                "ai_available": claude.client is not None,
            })
        if path == "/api/leaderboard":
            boards = store.leaderboard()
            for runs in boards.values():
                for r in runs:
                    r["model_label"] = MODELS.get(r.get("model"), {}).get("label", "Solo" if r.get("model") == "none" else "")
            return self.send(200, boards)
        if path.startswith("/api/blackbox/mission/"):
            return self.send(200, _get(sessions["blackbox"], path.rsplit("/", 1)[1], "mission").status())
        if path.startswith("/api/autopilot/"):
            return self.send(200, _get(sessions["autopilot"], path.rsplit("/", 1)[1], "autopilot link").status())
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
        if path.startswith(("/bb/", "/ap/")):
            return self.remote_post(path)
        if not self.authed():
            raise HttpError(401, "not logged in")
        if path == "/sim/bridge/sync":
            raise HttpError(404, "no local bridge on the hosted server")

        routes = {
            "/api/beginner/start": beginner_start,
            "/api/beginner/capcom": beginner_capcom,
            "/api/beginner/finish": beginner_finish,
            "/api/blackbox/mission": lambda body: new_session("blackbox", body),
            "/api/autopilot": lambda body: new_session("autopilot", body),
        }
        if path in routes:
            body = self.read_json()
            if not isinstance(body, dict):
                raise HttpError(400, "body must be a JSON object")
            return self.send(200, routes[path](body))
        m = re.match(r"^/api/autopilot/([\w-]+)/launch$", path)
        if m:  # the LAUNCH button in the participant's browser
            return self.send(200, _get(sessions["autopilot"], m.group(1), "autopilot link").launch())
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

    # ---- Remote sessions: /bb/<id>/... (Black Box) and /ap/<id>/... (Autopilot). The id is the credential. ----

    def _session(self, path):
        parts = path.split("/")  # ['', 'bb' or 'ap', id, ...]
        if len(parts) < 3:
            raise HttpError(404, "not found")
        kind, what = ("blackbox", "mission") if parts[1] == "bb" else ("autopilot", "autopilot link")
        return _get(sessions[kind], parts[2], what), "/" + "/".join(parts[3:])

    def remote_get(self, path, query):
        s, sub = self._session(path)
        sub = sub.rstrip("/") or "/"
        if isinstance(s, BlackBoxMission):
            if sub in ("/", "/api"):
                return self.send(200, {"endpoints": ["GET /api/state", "POST /api/command"], "commands": AGENT_COMMANDS,
                                       "docs": "See the Black Box briefing in hackathon/CHALLENGES.md"})
            if sub == "/api/state":
                s.require_tab()
                return self.send(200, dict(s.agent_state(s.bridge.state), connected=True))
        elif sub in ("/", "/api"):
            return self.send(200, {
                "ok": True, "callsign": s.callsign,
                "usage": 'POST /api/request with a JSON body: ' + REQUEST_HELP,
                "docs": "AUTOPILOT_API.md in the starter kit (/kit.zip)",
                "tab_connected": s.bridge.connected(), "flight": s.public_flight()})
        raise HttpError(404, "not found")

    def remote_post(self, path):
        s, sub = self._session(path)
        body = self.read_json()
        if sub == "/bridge/sync":  # the participant's browser tab
            if not self.authed():
                raise HttpError(401, "not logged in")
            commands = s.sync((body or {}).get("state"), (body or {}).get("results", []))
            return self.send(200, {"commands": commands, "session": s.status()})
        if isinstance(s, BlackBoxMission):
            if sub == "/api/command":
                return self.send(200, s.command(body))
        else:
            if sub == "/api/request":
                return self.send(200, s.request(body))
        raise HttpError(404, "not found")


def main():
    if not PASSWORD:
        sys.exit("Set HACKATHON_PASSWORD (the password participants will type).")
    if claude.client is None:
        print("WARNING: ANTHROPIC_API_KEY is not set; the AI CAPCOM won't work.")
    threading.Thread(target=watchdog, daemon=True).start()
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

"""Tiny persistent store: one JSON file, rewritten atomically after every change.

Holds the leaderboard (finished runs), per-callsign memory for CAPCOM and the AI pilot,
and today's API spend. Good for a hackathon's worth of data (thousands of runs).
"""

import json
import os
import threading
import time
import uuid


def callsign_key(callsign):
    return " ".join(callsign.lower().split())


class Store:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.data = {"runs": [], "profiles": {}, "spend": {"day": "", "usd": 0.0}}
        if os.path.exists(path):
            with open(path) as f:
                self.data.update(json.load(f))
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.data, f, indent=1)
        os.replace(tmp, self.path)

    # ---- runs / leaderboard ----

    def add_run(self, **run):
        """Record a finished attempt (any outcome). Only successes appear on the leaderboard."""
        run = dict(run, id=uuid.uuid4().hex[:12], created=time.time())
        with self.lock:
            self.data["runs"].append(run)
            self._save()
        return run

    def delete_run(self, run_id):
        with self.lock:
            before = len(self.data["runs"])
            self.data["runs"] = [r for r in self.data["runs"] if r["id"] != run_id]
            self._save()
            return len(self.data["runs"]) < before

    def runs(self):
        with self.lock:
            return list(self.data["runs"])

    def leaderboard(self, limit=10):
        """Best successful time per callsign, per tier and overall."""
        boards = {}
        for tier in ("beginner", "intermediate", "advanced", "overall"):
            best = {}
            for r in self.runs():
                if r.get("outcome") != "success" or (tier != "overall" and r["tier"] != tier):
                    continue
                k = callsign_key(r["callsign"])
                if k not in best or r["time_s"] < best[k]["time_s"]:
                    best[k] = r
            boards[tier] = sorted(best.values(), key=lambda r: r["time_s"])[:limit]
        return boards

    def rank(self, tier, time_s):
        """1-based position a time would take on a tier's board."""
        return 1 + sum(1 for r in self.leaderboard(limit=10**6)[tier] if r["time_s"] < time_s)

    # ---- per-callsign memory ----

    def profile(self, callsign):
        with self.lock:
            p = self.data["profiles"].get(callsign_key(callsign), {})
            return json.loads(json.dumps(p))  # deep copy

    def update_profile(self, callsign, fn):
        """fn(profile_dict) mutates the profile in place."""
        with self.lock:
            p = self.data["profiles"].setdefault(callsign_key(callsign), {"callsign": callsign})
            fn(p)
            self._save()

    # ---- spend tracking ----

    def add_spend(self, usd):
        day = time.strftime("%Y-%m-%d", time.gmtime())
        with self.lock:
            s = self.data["spend"]
            if s.get("day") != day:
                s["day"], s["usd"] = day, 0.0
            s["usd"] += usd
            self._save()
            return s["usd"]

    def spend_today(self):
        day = time.strftime("%Y-%m-%d", time.gmtime())
        with self.lock:
            s = self.data["spend"]
            return s["usd"] if s.get("day") == day else 0.0

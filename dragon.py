"""Tiny Python client for the docking simulator's HTTP API (standard library only).

    from dragon import Dragon

    sim = Dragon()                 # talks to http://localhost:5555 (start server.py first)
    sim.reset()                    # new attempt; returns once the vehicle is controllable
    s = sim.state()
    print(s["position"], s["attitude"])
    sim.pitch_up(2)                # two rotation pulses
    sim.translate_forward()        # one translation pulse

Every command returns the vehicle state right after the command took effect.
See API.md for what the fields mean.
"""

import json
import urllib.error
import urllib.request


class DragonError(Exception):
    pass


class Dragon:
    def __init__(self, url="http://localhost:5555"):
        self.url = url.rstrip("/")

    def _request(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            try:
                msg = json.load(e).get("error", str(e))
            except ValueError:
                msg = str(e)
            raise DragonError(msg) from None
        except urllib.error.URLError as e:
            raise DragonError("can't reach %s (is server.py running?): %s" % (self.url, e.reason)) from None

    def state(self):
        """Current vehicle state as a dict."""
        return self._request("/api/state")

    def command(self, name, **args):
        """Run one command, e.g. command("yaw_left", count=3). Returns the state afterwards."""
        result = self._request("/api/command", dict(args, command=name))
        if not result.get("ok"):
            raise DragonError(result.get("error"))
        return result["state"]

    def commands(self, *cmds):
        """Run several commands in order in one round trip.
        Each item is a name or a dict: commands("pitch_up", {"command": "yaw_left", "count": 2})."""
        body = [{"command": c} if isinstance(c, str) else c for c in cmds]
        results = self._request("/api/command", body)
        for r in results:
            if not r.get("ok"):
                raise DragonError("%s: %s" % (r.get("command"), r.get("error")))
        return results[-1]["state"]

    # Session control
    def reset(self): return self.command("reset")
    def pause(self): return self.command("pause")
    def resume(self): return self.command("resume")
    def step(self, seconds=1.0): return self.command("step", seconds=seconds)

    # Rotation pulses (each changes the rotation rate about one axis)
    def roll_left(self, count=1): return self.command("roll_left", count=count)
    def roll_right(self, count=1): return self.command("roll_right", count=count)
    def pitch_up(self, count=1): return self.command("pitch_up", count=count)
    def pitch_down(self, count=1): return self.command("pitch_down", count=count)
    def yaw_left(self, count=1): return self.command("yaw_left", count=count)
    def yaw_right(self, count=1): return self.command("yaw_right", count=count)

    # Translation pulses (each changes velocity along the vehicle's own axes)
    def translate_forward(self, count=1): return self.command("translate_forward", count=count)
    def translate_backward(self, count=1): return self.command("translate_backward", count=count)
    def translate_left(self, count=1): return self.command("translate_left", count=count)
    def translate_right(self, count=1): return self.command("translate_right", count=count)
    def translate_up(self, count=1): return self.command("translate_up", count=count)
    def translate_down(self, count=1): return self.command("translate_down", count=count)

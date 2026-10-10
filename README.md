# SpaceX ISS Docking Simulator, offline and scriptable

Run the [SpaceX ISS Docking Simulator](https://iss-sim.spacex.com/) locally, and fly it by hand **or from code**. A small HTTP API and a dependency-free Python client let you read the spacecraft's state and fire its thrusters. Use them to write autopilots, test control algorithms, or let an AI agent attempt the docking.

## Quick start

1. Clone this repository:
    ```bash
    git clone https://github.com/matthewgiarra/spacex-iss-sim
    cd spacex-iss-sim
    ```

2. Start the local server (Python 3.8+, no packages needed):
    ```bash
    python server.py
    ```

3. Open [http://localhost:5555](http://localhost:5555) in a browser.

That should be it. The simulator loads in your browser window, and you can fly it with the on-screen buttons or the keyboard. Have at it.

> Just want to play? Any static file server works too, e.g. `python -m http.server 5555` from the repo root, then open [http://localhost:5555/iss-sim.spacex.com](http://localhost:5555/iss-sim.spacex.com).

## Fly it from code

With the page open in a browser, `server.py` also exposes the simulator as a JSON API:

```bash
curl -X POST localhost:5555/api/command -d '{"command": "reset"}'                 # start an attempt
curl localhost:5555/api/state                                                     # read the state
curl -X POST localhost:5555/api/command -d '{"command": "pitch_up", "count": 2}'  # fire thrusters
```

Or use the Python client, [`dragon.py`](dragon.py), a single file that uses only the standard library:

```python
from dragon import Dragon

sim = Dragon()
s = sim.reset()                # returns once the vehicle is controllable
print(s["position"])           # {'x': 200, 'y': 12, 'z': 30}   metres from the docking port
print(s["attitude"])           # {'roll': 15, 'pitch': -20, 'yaw': -10}   degrees

s = sim.roll_right(3)          # every command returns the state right after it
s = sim.translate_forward()
```

To see a complete docking, run the example autopilot, a simple feedback controller with no AI:

```bash
python examples/autopilot.py
```

### Turn-based mode

Slow controllers, such as anything that calls an LLM, can pause the simulation and advance it in exact steps. The vehicle then doesn't drift while they think:

```python
sim.pause()
while s["status"] == "flying":
    # ...look at s and decide what to fire...
    s = sim.commands({"command": "yaw_left"}, {"command": "step", "seconds": 0.5})
```

**[API.md](API.md)** is the full reference. It covers the state fields, every command, the coordinate frame, the docking criteria, the Python client, and common gotchas.

## How it works

```
 your code ──HTTP/JSON──▶ server.py ◀──polls every ~30 ms── browser tab (the actual simulation)
 (any language,           (serves the page,                 js/api.js exposes window.dragon:
  or dragon.py)            relays commands/state)           getState(), command())
```

The simulation still runs in the browser, exactly as on SpaceX's site. [`js/api.js`](iss-sim.spacex.com/js/api.js) adds a small `window.dragon` interface to the page and syncs with `server.py`. You can also call `window.dragon` directly from the devtools console or from browser-automation tools like Playwright.

## Changes from the original simulator

- **Fixed-rate physics.** The original advanced the physics once per rendered frame, so it ran 2.4× faster on a 144 Hz display than on a 60 Hz one. Physics now steps at a fixed 60 Hz regardless of display, which also makes pause and step possible.
- **Programmatic interface.** Added [`js/api.js`](iss-sim.spacex.com/js/api.js), [`server.py`](server.py), [`dragon.py`](dragon.py), [`API.md`](API.md) and [`examples/autopilot.py`](examples/autopilot.py).

Manual play is otherwise unchanged.

## Run it as a hackathon challenge

[`hackathon/`](hackathon/README.md) turns the simulator into a hosted, password-protected booth with three AI challenges and a big-screen leaderboard:

- **Beginner: AI CAPCOM.** You fly while an AI capsule communicator talks you in and remembers your previous attempts.
- **Intermediate: Role Reversal.** An AI pilot flies and you're its CAPCOM, with limited radio calls and standing orders.
- **Advanced: Black Box Dragon.** Your own AI agent flies over HTTP with unlabeled, scrambled thrusters and position-only sensors.

The first two need only a browser. [hackathon/README.md](hackathon/README.md) covers setting up an Anthropic API key with auto-reload and spend limits, and deploying to Render. [hackathon/CHALLENGES.md](hackathon/CHALLENGES.md) is the participant guide.

## Repository layout

| Path | What it is |
|---|---|
| `iss-sim.spacex.com/` | The simulator: HTML, three.js scene, models, textures |
| `iss-sim.spacex.com/js/api.js` | In-page API (`window.dragon`) and bridge to `server.py` |
| `server.py` | Static file server + JSON API (`/api/state`, `/api/command`) |
| `dragon.py` | Python client |
| `examples/autopilot.py` | Reference controller that docks successfully |
| `API.md` | API reference |
| `hackathon/` | Hosted hackathon version: password-protected, with AI CAPCOM, an AI pilot, the Black Box challenge and a leaderboard ([guide](hackathon/README.md)) |

## Tips

- **Keep the simulator tab visible while scripting.** Browsers freeze background tabs, minimized windows, and on Windows even windows completely covered by another window. Use one tab only. For unattended runs, see the headless-browser note in [API.md](API.md#gotchas).
- `python server.py --port 8000` changes the port, and `--host 0.0.0.0` lets other machines on your network connect.

The simulator itself is SpaceX's work. This repository hosts an offline copy of it.

# Docking Simulator API

Fly the SpaceX Dragon docking simulator from code: read the vehicle's state, fire thrusters, and try to dock with the ISS.

## Quick start

```bash
python server.py
```

Open **http://localhost:5555** in a browser and **leave the tab visible** (the simulation runs in the page and browsers freeze hidden tabs). Then, from anywhere:

```bash
curl -X POST localhost:5555/api/command -d '{"command": "reset"}'        # start an attempt (~10 s)
curl localhost:5555/api/state                                            # read the state
curl -X POST localhost:5555/api/command -d '{"command": "pitch_up", "count": 3}'
```

Or with the Python client (standard library only):

```python
from dragon import Dragon

sim = Dragon()
s = sim.reset()
print(s["attitude"])        # {'roll': 15.0, 'pitch': -20.0, 'yaw': -10.0}
s = sim.roll_right(3)       # every command returns the state right after it
```

`python examples/autopilot.py` flies a complete docking with a simple non-AI controller. It is the baseline to beat.

## The task

You start 200 m from the ISS, slightly off-axis and tilted. To dock, reach the docking port with **all** of these true at the moment of contact:

| Condition | Limit |
|---|---|
| `range` | < 0.2 m (this is what triggers the check) |
| `attitude.roll`, `.pitch`, `.yaw` | each within ±0.2° |
| `velocity.x`, `.y`, `.z` | each within ±0.24 m/s |

You **fail** if you arrive outside those limits, touch the station anywhere else, or drift more than 500 m away. There is no time limit.

## Coordinates

Everything is measured relative to the ISS docking port, using the same axes as the on-screen HUD:

- **x**: distance along the docking axis. Starts at 200 m and must reach 0.
- **y**: left/right offset. Starts at 12 m.
- **z**: up/down offset. Starts at 30 m.

`attitude` is in degrees. All zeros means pointing straight at the port, which is where you need to be.

**Translation commands push along the vehicle's own axes, not the x/y/z above.** Once attitude is near zero the two line up:

| Command | Effect when aligned |
|---|---|
| `translate_forward` / `translate_backward` | x decreases / increases |
| `translate_right` / `translate_left` | y increases / decreases |
| `translate_up` / `translate_down` | z increases / decreases |

If you're still tilted, a "forward" pulse will push partly sideways too. Fix attitude first.

## How the controls behave

Like the real thing, thrusters fire in **pulses** and there is no friction or drag:

- **Translation pulse:** changes velocity by 0.06 m/s (fine, default) or 0.30 m/s (coarse). The vehicle keeps drifting until you fire the opposite way.
- **Rotation pulse:** changes rotation rate by 0.3°/s (fine) or 0.6°/s (coarse). Rotation also keeps going until countered. The actual rate eases toward the new value over about 1 s; `rotation_rate_commanded` shows where it is heading.

Rotation pulses increase or decrease the matching attitude angle:

| Increases | Decreases |
|---|---|
| `roll_left` | `roll_right` |
| `pitch_up` | `pitch_down` |
| `yaw_left` | `yaw_right` |

The three rotations interact: turning about one axis while tilted on another shifts the other angles a little. Keep watching and correcting.

**Gravity** (on by default) makes you sink slowly, at 0.006 m/s in z. This drift is *not* included in `velocity`, and it doesn't count toward the docking speed limit.

## Time, pause, and step

The simulation runs at a fixed 60 steps per second of sim time, whatever your screen's frame rate.

By default it runs in real time. If your agent is slow (an LLM call can take seconds), the vehicle keeps drifting while it thinks. To stop that, use turn-based mode:

```python
sim.pause()
while True:
    s = sim.state()
    # ...decide, fire thrusters...
    sim.step(0.5)            # advance exactly 0.5 s of sim time, then freeze again
```

`step` usually runs much faster than real time. `resume` goes back to real time. Commands work while paused and take effect on the next step.

## State reference

`GET /api/state` (or `sim.state()`). Values are rounded to a few decimals.

```json
{
  "status": "flying",
  "message": "",
  "paused": false,
  "time_s": 12.5,
  "position": {"x": 200.0, "y": 12.0, "z": 30.0},
  "velocity": {"x": 0.0, "y": 0.0, "z": 0.0},
  "range": 202.593,
  "range_rate": 0.0,
  "attitude": {"roll": 15.0, "pitch": -20.0, "yaw": -10.0},
  "rotation_rate": {"roll": 0.0, "pitch": 0.0, "yaw": 0.0},
  "rotation_rate_commanded": {"roll": 0.0, "pitch": 0.0, "yaw": 0.0},
  "precision": {"translation": "fine", "rotation": "fine"},
  "gravity": true,
  "connected": true
}
```

| Field | Meaning |
|---|---|
| `status` | `loading` → `intro` → `arriving` → `flying` → `success` or `fail`. Only `flying` accepts thruster commands. |
| `message` | Why the attempt ended, e.g. `"The following errors occurred: SPEED"`. |
| `paused` | True while in pause/step mode. |
| `time_s` | Sim seconds since this attempt began. |
| `position` | Metres from the docking port (see Coordinates). |
| `velocity` | m/s, same axes. Excludes gravity drift. |
| `range` | Straight-line distance to the port, in metres. |
| `range_rate` | m/s. Negative means closing. |
| `attitude` | Degrees. Target is 0 on all three. |
| `rotation_rate` | Current rotation rate, deg/s. |
| `rotation_rate_commanded` | The rate each axis is easing toward, deg/s. |
| `precision` | Current pulse size for each control group. |
| `gravity` | Whether gravity drift is on. |
| `connected` | Added by the server. Always true in a successful response. |

Before the vehicle has arrived (`loading`, `intro`, and the first part of `arriving`), only `status`, `message`, `paused`, and `time_s` are present.

## Command reference

`POST /api/command` with a JSON body `{"command": "<name>", ...options}`. It returns once the command has taken effect:

```json
{"ok": true, "command": "pitch_up", "state": { ...same as /api/state... }}
{"ok": false, "command": "pitch_up", "error": "'pitch_up' only works while status is 'flying' (status is 'intro')", "state": {...}}
```

To send several commands in one round trip, post a JSON **list**. They run in order and you get back a list of results. For example, fire and then advance time:

```bash
curl -X POST localhost:5555/api/command -d '[{"command":"yaw_left","count":2},{"command":"translate_forward"},{"command":"step","seconds":1}]'
```

| Command | Options | Description |
|---|---|---|
| `roll_left`, `roll_right`, `pitch_up`, `pitch_down`, `yaw_left`, `yaw_right` | `count` (1–50, default 1) | Rotation pulses. |
| `translate_forward`, `translate_backward`, `translate_left`, `translate_right`, `translate_up`, `translate_down` | `count` (1–50, default 1) | Translation pulses. |
| `translation_fine`, `translation_coarse` | | Set the translation pulse size to 0.06 or 0.30 m/s. |
| `rotation_fine`, `rotation_coarse` | | Set the rotation pulse size to 0.3 or 0.6 °/s. |
| `gravity_on`, `gravity_off` | | Toggle the gravity drift. It's on by default; leave it on for official runs. |
| `reset` | | Start a fresh attempt from any status. Returns when `flying` (up to ~15 s). Keeps pause mode as it was. |
| `pause`, `resume` | | Freeze sim time, or go back to real time. |
| `step` | `seconds` (0–60, default 1) | While paused, advance sim time by exactly this much. |

`GET /api` lists the commands.

HTTP status codes: `200` means the command ran (check `ok`). `400` means a malformed body. `503` means no simulator tab is connected. `504` means the tab stopped responding.

## Python client reference

[`dragon.py`](dragon.py) is a single file with no dependencies. Copy it into your project or import it from the repo root.

```python
from dragon import Dragon, DragonError

sim = Dragon("http://localhost:5555")   # the default URL
```

| Method | Returns | Notes |
|---|---|---|
| `state()` | state dict | Same as `GET /api/state`. |
| `command(name, **options)` | state dict after the command | For example `command("step", seconds=2)` or `command("yaw_left", count=3)`. |
| `commands(*cmds)` | state dict after the last one | Runs several commands in one round trip. Each item is a name or a dict: `commands("pitch_up", {"command": "step", "seconds": 1})`. |
| `reset()` | state dict | Starts a new attempt from any status, and returns once `flying`. |
| `pause()`, `resume()`, `step(seconds=1.0)` | state dict | Turn-based mode (see above). |
| `roll_left(count=1)`, `roll_right`, `pitch_up`, `pitch_down`, `yaw_left`, `yaw_right` | state dict | Rotation pulses. |
| `translate_forward(count=1)`, `translate_backward`, `translate_left`, `translate_right`, `translate_up`, `translate_down` | state dict | Translation pulses. |

Any failure raises `DragonError` with a readable message: an invalid command, a command sent outside `flying`, the server not running, or no browser tab connected.

A minimal turn-based control loop:

```python
sim = Dragon()
sim.reset()
sim.pause()
s = sim.state()
while s["status"] == "flying":
    cmds = []
    if s["attitude"]["pitch"] < -0.5 and s["rotation_rate_commanded"]["pitch"] <= 0:
        cmds.append({"command": "pitch_up"})
    # ...more decisions...
    s = sim.commands(*cmds, {"command": "step", "seconds": 0.2})
print(s["status"], s["message"])
```

See [`examples/autopilot.py`](examples/autopilot.py) for a complete controller that docks.

## Other ways in

- **Browser JavaScript:** the page exposes `window.dragon`, with `dragon.getState()` and `dragon.command("pitch_up", {count: 2})` (returns a Promise). You can use it from the devtools console, Playwright/Puppeteer `page.evaluate`, or a browser-automation agent. It works without `server.py`.
- **Screen and mouse only:** browser-using AI agents can also just fly the normal UI, with the on-screen buttons or the keyboard. Keys: `Q`/`E` backward/forward, `A`/`D` left/right, `W`/`S` up/down, arrow keys for pitch and yaw, `,`/`.` for roll. A pulse fires when the key is released.
- **Any language:** it's plain JSON over HTTP.

## Gotchas

- **Keep exactly one simulator tab open, and keep it visible.** Browsers pause background tabs, minimized windows, and on Windows even windows completely covered by another window. That stops the sim, and commands time out with `the simulator tab stopped responding`. Put the browser side by side with your terminal or editor. With two tabs open, they'll steal each other's commands.
- **For unattended runs, use a headless browser:**
  `msedge --headless=new --enable-unsafe-swiftshader --disable-background-timer-throttling --disable-renderer-backgrounding --disable-backgrounding-occluded-windows http://localhost:5555` (or `chrome` with the same flags). Rendering is slow without a GPU, but pause/step still runs faster than real time.
- **On Windows PowerShell**, `curl` is an alias for `Invoke-WebRequest`. Use `curl.exe` and escape the inner quotes, or just use the Python client.
- **Velocities can be very small numbers:** `0.06` is one fine pulse, and the docking limit is `0.24` (4 pulses).
- **Reloading the page** resets everything, including pause mode and gravity.
- To let other machines on the network connect, run `python server.py --host 0.0.0.0`.

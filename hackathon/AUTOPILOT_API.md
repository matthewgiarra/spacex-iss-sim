# Autopilot API

Write a program that docks SpaceX's Dragon with the International Space Station, in any language. The simulator runs in real time in your browser. Your autopilot asks for the latest telemetry, decides, and sends thruster inputs, over and over, until it docks.

```
 your autopilot  ── request / reply (HTTPS) ──▶  challenge website  ──▶  simulator tab
 (any language)     "telemetry?"  "fire these"   (scores every flight)    (your browser)
```

Every request is one JSON object, sent to your private **autopilot URL**, and gets one JSON reply. The starter kit's client libraries (Python, C, C++) do this for you.

## Contents

- [Quick start](#quick-start)
- [The control loop](#the-control-loop)
- [Requests and replies](#requests-and-replies)
- [State and control inputs](#state-and-control-inputs)
- [Timing](#timing)
- [Rules](#rules)
- [Coordinates and signs](#coordinates-and-signs)
- [Client libraries](#client-libraries): Python, C, C++, anything else
- [Troubleshooting](#troubleshooting)

---

## Quick start

For Python you need **Python 3.8 or newer** and nothing else: the client uses only the standard library. Check with `python --version` (on some Macs, `python3`). On Windows without admin rights, install Python from [python.org](https://www.python.org/downloads/windows/) and **untick "Install launcher for all users"**, or use the Microsoft Store version. For C and C++, see [Client libraries](#client-libraries).

1. On the challenge website, open **Autopilot**, type your callsign, and press **GET AUTOPILOT LINK**. Keep that tab open and visible: your Dragon flies there.
2. Copy your **autopilot URL** from the panel (`https://<site>/ap/<your-key>`). It's the key to your flights, so keep it to yourself.
3. In a terminal, in the starter kit folder, start your autopilot with that URL. For the Python example:
   ```bash
   python autopilot.py https://<site>/ap/<your-key>
   ```
   The simulator tab shows **Autopilot connected** while your program is sending requests.
4. Press **LAUNCH** in the tab (or add `--launch` to the command). The Dragon arrives at the start point in a few seconds, then your autopilot has control.

Edit, run again, launch again.

---

## The control loop

Every autopilot has the same shape, whatever the language:

```python
import time
import hackathon.client as hackathon_client

connection = hackathon_client.connect("https://<site>/ap/<your-key>")
controller = MyController()          # yours: PID, state machine, MPC...
flight_id, timestamp_ms = None, 0

while True:
    data = connection.request_telemetry()
    if flight_id is None:                       # wait for a flight to start
        if data["status"] == "flying":
            flight_id, timestamp_ms = data["flight_id"], data["timestamp_ms"]
        else:
            time.sleep(0.05)
        continue
    if data["status"] != "flying" or data["flight_id"] != flight_id:
        print("docked!" if data["docked"] else data["message"])   # the flight is over
        break

    dt = data["timestamp_ms"] - timestamp_ms
    if dt == 0:                                 # same telemetry as last time: nothing new yet
        time.sleep(0.010)
        continue
    timestamp_ms = data["timestamp_ms"]

    inputs = controller.update(data["state"], dt)                 # 6 numbers: x, y, z, roll, pitch, yaw
    if any(inputs):
        connection.send_control_inputs(inputs)
```

`autopilot.py`, `examples/autopilot.c` and `examples/autopilot.cpp` in the kit are complete, runnable versions of this loop.

---

## Requests and replies

Send each request as an HTTP `POST` to **`<your autopilot URL>/api/request`** with a JSON body. Send one request, wait for its reply, repeat. Every reply is JSON with `"ok"`. When `ok` is `false`, `"error"` says why, and nothing happened. An HTTP status other than 200 means the request didn't get through (404: unknown or expired autopilot URL).

Add `"client": "<any id>"` to every request, with an id your program picks once at startup (the kit's clients do this for you). It lets the website keep track of what it has shown each run of your program; see [Timing](#timing).

### `telemetry`: the latest state

```json
{"request": "telemetry"}
```

```json
{
  "ok": true,
  "status": "flying",
  "docked": false,
  "flight_id": "09db66a62e0b",
  "timestamp_ms": 52310,
  "state": [122.37, 9.81, 20.4, -1.59, -0.04, -0.12, -0.06, 0.1, -0.05, 0.0, -0.01, 0.0],
  "commanded_rates": [0.0, 0.0, 0.0],
  "rank": null,
  "message": ""
}
```

| Field | Meaning |
|---|---|
| `status` | Where things stand: see the table below. You have control only while it's `"flying"`. |
| `docked` | `true` once you've docked. |
| `flight_id` | Identifies the flight. A new one each LAUNCH; `null` before the first. |
| `timestamp_ms` | Simulator time in this flight, in milliseconds. Starts near 0 at each launch. **The same value as last time means nothing new.** At the end of a flight it's the final time, which is your score if you docked. |
| `state` | 12 numbers (see [State](#state-and-control-inputs)), or `null` when there's no flight data. |
| `commanded_rates` | `[roll, pitch, yaw]`: the rotation rates (deg/s) the Dragon is easing toward. Rotation rates lag your inputs by about a second; this tells you where they're heading. |
| `rank` | Your leaderboard place, after you dock. |
| `message` | Why a flight ended (for example, `"You broke the approach corridor: ..."`), or what's going on. |

| `status` | Meaning |
|---|---|
| `waiting` | No flight. Press LAUNCH or send `launch`. |
| `launching` | The Dragon is flying to the start point (a few seconds). |
| `flying` | Your autopilot has control. |
| `docked` | Docked. `timestamp_ms` is your time. |
| `failed` | The flight ended without docking; `message` says why. |
| `offline` | No flight, and the simulator tab isn't open or visible. |

`docked` and `failed` describe the flight your program was flying. A program that starts after a flight ended (a different `client` id) sees `waiting`.

### `control`: fire thrusters

```json
{"request": "control", "inputs": [-2, 0, 1, 0, 0, -1]}
```

```json
{"ok": true, "timestamp_ms": 52344, "pulses": [-2, 0, 1, 0, 0, -1]}
```

`inputs` is `[x, y, z, roll, pitch, yaw]`: how many thruster pulses to fire on each axis (see [Control inputs](#state-and-control-inputs)). The reply's `pulses` are the whole pulses actually fired, and `timestamp_ms` is the simulator time they took effect. All zeros is allowed and does nothing.

Fails with `ok: false` when no flight is flying (`"no flight in progress (status: ...)"`) or the inputs aren't 6 finite numbers.

### `launch`: start a flight

```json
{"request": "launch"}
```

```json
{"ok": true, "flight_id": "09db66a62e0b"}
```

The same as pressing LAUNCH. Returns at once. `status` is `launching` for a few seconds, then `flying`. Fails if a flight is already in progress or the simulator tab isn't open.

### `abort`: end the current flight

```json
{"request": "abort", "reason": "lost my nerve"}
```

```json
{"ok": true}
```

Ends the flight now. It counts as a failed attempt, and the reason shows in the simulator tab.

---

## State and control inputs

**State** (`state`, 12 numbers; metres, m/s, degrees, deg/s):

| Index | Name | Meaning |
|---|---|---|
| 0, 1, 2 | `x`, `y`, `z` | Position relative to the docking port. `x` is the distance along the docking axis (about 200 at the start; it must reach 0). `y` is left/right (+ is right). `z` is up/down (+ is up). |
| 3, 4, 5 | `x_dot`, `y_dot`, `z_dot` | Velocity on the same axes, from your thrusters. (Gravity also pulls you down by about 0.006 m/s; it isn't included here.) |
| 6, 7, 8 | `roll`, `pitch`, `yaw` | Attitude. 0, 0, 0 means pointing straight at the port. |
| 9, 10, 11 | `roll_dot`, `pitch_dot`, `yaw_dot` | Rotation rates. |

Range (distance to the port) is `sqrt(x² + y² + z²)`. Closing speed is `-(x·x_dot + y·y_dot + z·z_dot) / range`.

**Control inputs** (`inputs`, 6 numbers): `[x, y, z, roll, pitch, yaw]`, each a number of thruster pulses.

| Axis | One pulse changes | +1 does | −1 does |
|---|---|---|---|
| `x` | `x_dot` by 0.06 m/s | moves you away from the station | moves you toward it |
| `y` | `y_dot` by 0.06 m/s | right | left |
| `z` | `z_dot` by 0.06 m/s | up | down |
| `roll` | the roll rate by 0.3 deg/s | rolls left (roll increases) | rolls right |
| `pitch` | the pitch rate by 0.3 deg/s | pitches up (pitch increases) | pitches down |
| `yaw` | the yaw rate by 0.3 deg/s | yaws left (yaw increases) | yaws right |

- Inputs are rounded to whole pulses (2.4 fires 2) and limited to ±50 per axis per request.
- **Velocities persist.** There's no friction: a pulse changes your speed until another pulse changes it back. To stop, fire the opposite way.
- Translation velocity changes immediately. Rotation rates ease toward their new value over about a second; `commanded_rates` shows the value they're heading for.
- **Translations push along the Dragon's own axes**, so they line up with `x`/`y`/`z` only when the attitude is near 0. Fix the attitude first.

---

## Timing

- **The simulator runs in real time.** It doesn't wait for your autopilot: the Dragon keeps moving while you think, and a slow loop flies a worse flight.
- **Each request is a network round trip** to the challenge website, typically 30 to 200 ms depending on your connection. The kit's clients keep one connection open, so you don't pay for a new connection each time.
- The simulator tab reports its state about **30 times a second**. Asking more often returns the same telemetry (same `timestamp_ms`); sleep about 10 ms and ask again.
- The `control` request waits until your inputs have taken effect in the simulator.
- **After a `control` request, telemetry never predates it.** The website keeps returning the telemetry you already have until the simulator reports a moment after your inputs took effect. So the next new telemetry always shows the result of your burn, and you won't fire the same correction twice. (This is tracked per `client` id.)
- `timestamp_ms` is simulator time and your score. A big jump between new readings means your program (or the network) stalled.

---

## Rules

**To dock**, reach range < 0.2 m with all of these true at that moment:

| Condition | Limit |
|---|---|
| `roll`, `pitch`, `yaw` | each within ±0.2° |
| `x_dot`, `y_dot`, `z_dot` | each within ±0.24 m/s |

**You fail** if you:

- touch the station anywhere other than the docking port,
- drift more than 500 m away,
- break the **approach corridor**: your closing speed must never exceed **0.1 + 0.02 × range** m/s. That's 4.1 m/s at 200 m, 1.1 m/s at 50 m, 0.5 m/s at 20 m and 0.2 m/s at 5 m. It's checked 60 times a second,
- haven't docked after **600 s** of simulated time,
- or your simulator tab is closed or hidden for 30 seconds (the flight is abandoned and not scored).

**Scoring:** your best docking time (`timestamp_ms` when docked) per callsign goes on the Autopilot leaderboard. Failed flights aren't shown. Fly as many times as you like.

**Starting point:** about 200 m out, 12 m to the right, 30 m up; attitude roll 15°, pitch −20°, yaw −10°; at rest.

**Fair play:** your program flies; your hands don't. The manual controls are disabled on the Autopilot page. Don't modify the simulator page or its network traffic. Using AI to write your autopilot is not just allowed but encouraged.

---

## Coordinates and signs

```
            +z (up)
             │
             │      +y (right)
             │    ╱
             │  ╱
  ISS port ◀─┼──────────── +x (distance out along the docking axis; you start at x ≈ 200)
           origin
```

- A positive input on an axis increases that axis's velocity (or rotation rate). To close in, fire **negative** `x`.
- Roll, pitch and yaw follow the same rule: a positive `pitch` input makes `pitch` increase.
- These are the same axes as the X/Y/Z readouts on the simulator's display.

---

## Client libraries

The kit has ready-made clients for Python, C and C++. They take your autopilot URL, add the `client` id, keep the connection open between requests, and turn network failures into clear errors.

### Python: `hackathon.client`

Standard library only. Run your autopilot from the kit folder (or copy the `hackathon` folder next to it).

```python
import hackathon.client as hackathon_client

connection = hackathon_client.connect("https://<site>/ap/<your-key>")   # or connect(url, timeout_ms=10000)
data = connection.request_telemetry()                                   # dict, as in the reply above
reply = connection.send_control_inputs([0, 0, -2, 0, 1, 0])             # lists, tuples, numpy arrays
connection.launch()
connection.abort("reason")
```

Module-level versions work too: `hackathon_client.request_telemetry(connection)`, `hackathon_client.send_control_inputs(connection, inputs)`. When a request doesn't get through (no network, wrong or expired URL, timeout), these raise `hackathon_client.HackathonError`.

### C: `include/hackathon_client.h`

One header, C99. It uses the HTTPS library your system already has: WinHTTP on Windows (built in) and libcurl on macOS (built in) and Linux (install your distribution's libcurl development package, e.g. `libcurl4-openssl-dev`). Include it before other headers.

```c
#include "hackathon_client.h"

hk_connection *c = hk_connect("https://<site>/ap/<your-key>", 10000);   /* NULL if that isn't an autopilot URL */
hk_telemetry t;
hk_reply r;
if (hk_request_telemetry(c, &t) != 0) puts(hk_last_error(c));    /* -1: the request didn't get through */
/* t.ok, t.status, t.docked, t.flight_id, t.timestamp_ms, t.has_state, t.state[HK_X..HK_YAW_DOT],
   t.commanded_rates[3], t.rank, t.message, t.error */
double inputs[6] = {0, 0, -2, 0, 1, 0};
hk_send_control_inputs(c, inputs, &r);                    /* r.ok, r.timestamp_ms, r.pulses[6], r.error */
hk_launch(c, &r);
hk_abort(c, "reason", &r);
hk_sleep_ms(10);
hk_close(c);
```

```bash
cc -I include examples/autopilot.c -o autopilot -lcurl -lm         # macOS, Linux
gcc -I include examples/autopilot.c -o autopilot.exe -lwinhttp     # Windows (MinGW)
cl /I include examples\autopilot.c                                 # Windows (Visual Studio)
```

### C++: `include/hackathon_client.hpp`

C++11 or newer; wraps the C header (keep both files together).

```cpp
#include "hackathon_client.hpp"

hackathon::Client client("https://<site>/ap/<your-key>");    // or hackathon::connect(url, timeoutMs)
hackathon::Telemetry data = client.requestTelemetry();       // data.status, data.state[hackathon::X], ...
hackathon::Reply r = client.sendControlInputs({0, 0, -2, 0, 1, 0});
client.launch();
client.abort("reason");
hackathon::sleepMs(10);
```

Requests throw `hackathon::Error` when they don't get through.

```bash
c++ -std=c++11 -I include examples/autopilot.cpp -o autopilot -lcurl                # macOS, Linux
g++ -std=c++11 -I include examples/autopilot.cpp -o autopilot.exe -lwinhttp         # Windows (MinGW)
cl /EHsc /I include examples\autopilot.cpp                                          # Windows (Visual Studio)
```

### Anything else

Any language that can send an HTTPS POST works: JavaScript, Java, C#, Rust, Go, Julia, MATLAB... Reuse one connection (HTTP keep-alive) if your library allows it; it saves time on every request.

```bash
# curl
curl -s -X POST https://<site>/ap/<your-key>/api/request -H "Content-Type: application/json" -d '{"request": "telemetry", "client": "test"}'
```

```javascript
// JavaScript (Node.js 18 or newer, or a browser console), no packages
const url = "https://<site>/ap/<your-key>/api/request";
const ask = async (req) => (await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
                                               body: JSON.stringify({ ...req, client: "my-js-autopilot" }) })).json();
console.log(await ask({ request: "telemetry" }));
console.log(await ask({ request: "control", inputs: [0, 0, -1, 0, 0, 0] }));
```

```powershell
# PowerShell (Windows, nothing to install)
$url = "https://<site>/ap/<your-key>/api/request"
Invoke-RestMethod -Method Post -Uri $url -ContentType "application/json" -Body '{"request": "telemetry", "client": "ps"}'
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `No module named hackathon` | Run from the kit folder (the one containing the `hackathon` folder). |
| `unknown or expired autopilot URL` | Links last a few hours. Press GET AUTOPILOT LINK again and use the new URL. |
| `couldn't reach ...` | Check your internet connection and the URL (copy it again from the Autopilot page). |
| `couldn't verify the website's certificate` (Mac) | Python from python.org needs its certificates installed once: run `Install Certificates.command` in your Python folder (Applications → Python 3.x). |
| `status` is `offline` | The simulator tab isn't open, or the browser froze it because it's hidden. Open it and keep it visible. |
| `status` stays `waiting` after LAUNCH | Same: keep the simulator tab visible. |
| `no flight in progress` | You sent inputs before the flight started or after it ended. Check `status` first. |
| `timestamp_ms` doesn't change | Normal for a few tens of milliseconds (and after a `control` request, until the simulator catches up). If it's stuck for seconds, the simulator tab is hidden or closed. |
| C: `curl/curl.h: No such file` (Linux) | Install libcurl's development package, e.g. `sudo apt install libcurl4-openssl-dev`. |

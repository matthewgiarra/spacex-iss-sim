# Dragon Docking Challenge: participant guide

Dock SpaceX's Dragon capsule with the International Space Station as fast as you can, with AI on your side. There are three challenges at different levels, each with its own leaderboard, plus an overall board on the big screen at the booth.

You'll get the **website address** and a **password** at the booth.

| | Beginner: AI CAPCOM | Intermediate: Role Reversal | Advanced: Black Box Dragon |
|---|---|---|---|
| **Who flies** | You | An AI pilot | Your own AI agent |
| **How AI helps** | An AI CAPCOM talks you in | You coach the AI pilot | The AI works out an unknown spacecraft |
| **You need** | A laptop with a browser | A laptop with a browser | A laptop, an AI coding agent, and Python or PowerShell |
| **Time** | 5 to 10 minutes per attempt | 10 to 15 minutes per flight | 30 minutes or more |

## Before you come

- **Beginner and Intermediate:** nothing to install. Bring a laptop with an up-to-date Chrome, Edge or Firefox. A mouse helps but isn't required.
- **Advanced:** see [Advanced: setup](#setup) below. Install everything before you arrive; the booth Wi-Fi is not the place to download tools.

## The basics (all challenges)

You start about 200 m from the station's docking port, 12 m off to the side, 30 m too high, and tilted. To dock, you must touch the port with:

- range under 0.2 m,
- roll, pitch and yaw each within 0.2°,
- speed on each axis under 0.24 m/s.

Touching the station anywhere else, or drifting more than 500 m away, ends the attempt.

There's no friction. Every thruster pulse adds speed (or spin) that keeps going until you fire the opposite way. Gravity makes you sink very slowly. Most crashes come from forgetting to cancel a pulse.

**Keep the simulator tab visible while you fly.** Browsers pause hidden tabs, minimized windows, and on Windows even windows completely covered by another window.

---

## Beginner: AI CAPCOM

The CAPCOM (capsule communicator) is the one person in Mission Control who talks to the crew. Here it's an AI. It watches your telemetry, not your screen, and talks you in. It also remembers your previous attempts, so it can say things like "last time the speed got away from you inside ten metres".

### How to play

1. Go to the website, enter the password, and choose **Fly with AI CAPCOM**.
2. In the panel on the right, type a **callsign**. It's your name on the leaderboard, and CAPCOM uses it to remember you.
3. Choose a **CAPCOM** model, or **None** to fly solo.
4. Press **BEGIN** and fly. The clock starts when you get control and stops when you dock.
5. Type questions to CAPCOM in the box at the bottom ("what should I do first?", "am I too fast?"). Tick **Read radio calls aloud** to hear it.
6. After each attempt CAPCOM gives you a debrief. Press **PLAY AGAIN** and use it.

### Controls

| Key | Action | Key | Action |
|---|---|---|---|
| E / Q | Forward / backward | Up / Down arrows | Pitch up / down |
| A / D | Left / right | Left / Right arrows | Yaw left / right |
| W / S | Up / down | , / . | Roll left / right |

The on-screen buttons do the same. Each control group has a precision toggle: coarse pulses are bigger.

### Tips

- Fix the tilt first. Get roll, pitch and yaw to 0 and stop the rotation. Then line up sideways and vertically. Then approach.
- A good approach speed is about 1.5% of the range: 3 m/s at 200 m, 0.75 m/s at 50 m, 0.2 m/s at 10 m.
- **Experiment:** do you fly faster with Haiku, Sonnet or Opus as your CAPCOM, or solo? The leaderboard shows which model each time was flown with.

---

## Intermediate: Role Reversal

Now an **AI pilot flies** and **you are its CAPCOM**. On its own the pilot is not very good. Your coaching is what gets it docked, and fast.

### How to play

1. Choose **Be the CAPCOM** on the website.
2. Type a **callsign** and choose the **AI pilot** model.
3. Leave **standing orders** blank for your first flight. Press **LAUNCH AI PILOT** and watch what goes wrong.
4. While it flies, **radio** the pilot from the box at the bottom. You have **10 calls per flight**, so make them count. The simulation pauses while the pilot thinks, which gives you time to type.
5. After the flight the pilot gives you its **debrief**. Turn what you learned into **standing orders**: advice the pilot reads before every decision of the next flight.
6. Launch again. Keep refining.

### Rules

- **Approach corridor:** the closing speed must always stay under **0.1 + 0.02 × range** m/s (4.1 m/s at 200 m, 0.5 m/s at 20 m, 0.2 m/s at 5 m). Break it and the flight fails. The bar under the timer shows how close the pilot is to the limit.
- Your time is the AI's flight time. The leaderboard shows the model and how many radio calls you used.

### Tips for good coaching

- Rules of thumb beat joystick commands. "Never close faster than 1% of the range" helps for the whole flight; "fire forward twice" helps for one turn.
- Use numbers: "below 10 m, keep each velocity under 0.1 m/s".
- Watch for patterns: does it overshoot? Forget to stop rotating? Ignore the sideways offset?
- Try a different pilot model. Does a smarter pilot need less coaching, or different coaching?

---

## Advanced: Black Box Dragon

Your AI agent flies the Dragon over the internet, through an HTTP API. The catch: **the thrusters are unlabeled.** There are 14 of them, `T01` to `T14`. Each mission scrambles which is which, gives them different strengths, and some may not work at all. The **sensors report only position and attitude**, with no velocities or rotation rates. Your agent has to work out how the spacecraft responds, then dock, and **the clock runs the whole time**, across every attempt.

A plain "dock as fast as possible" prompt won't win this. How should the agent explore? What should it measure? Should it explore and fly at the same time? Those choices are where the creativity is, and different teams will find different answers.

<a id="setup"></a>
### Setup (do this before the event)

You need two things: an **AI agent** that can write and run code, and a way to **run** that code. Neither path below needs administrator rights on Windows.

**Option A (recommended): an AI coding agent plus Python**

1. **Python 3.8 or newer.** On Windows, download the installer from [python.org](https://www.python.org/downloads/windows/) and **untick "Install launcher for all users"** (which needs admin); everything else installs into your own user folder. The Microsoft Store version of Python also works without admin. Check it with `python --version`.
2. **An AI coding agent.** For example, [Claude Code](https://code.claude.com/docs/en/setup) (its installer works without admin; you need a Claude subscription or API credits), or the agent built into your editor (Cursor, VS Code with Copilot, and so on). Check that it can run a small Python script for you before the event.
3. Optional: download [`dragon.py`](../dragon.py), a tiny Python client that uses only the standard library.

**Option B (nothing to install): a chat AI plus PowerShell**

Every Windows laptop has PowerShell, which can call web APIs with `Invoke-RestMethod`. Use any chat AI in your browser (for example [claude.ai](https://claude.ai)) to write a PowerShell script, paste it into PowerShell, run it, and paste the output back to the AI. It's slower going, but it works anywhere.

### How to play

1. Choose **Open Mission Control** on the website, type a **team name**, and press **START NEW MISSION**.
2. The panel shows your mission's private **API base URL**, something like `https://<site>/bb/Xy3k...`. Give it to your agent, along with the briefing below. Anyone who has this URL can fly your mission, so don't share it.
3. Keep the Mission Control tab open and visible. That's where your Dragon actually flies, and you can watch it.
4. When your agent docks, the time goes on the leaderboard. Start a new mission (fresh scramble, clock back to zero) to try for a better time.

### Briefing for your agent

Paste this into your agent, filling in the base URL.

> You are flying a SpaceX Dragon spacecraft in a simulator, through an HTTP API at `BASE` (for example `BASE/api/state`). Goal: dock with the International Space Station in the least total simulated time.
>
> **Reading the state:** `GET BASE/api/state` returns JSON: `status` (`intro`, `arriving`, `flying`, `success` or `fail`), `message`, `paused`, `time_s` (sim seconds in this attempt), `mission_time_s` (sim seconds across all attempts, which is your score), `mission_complete`, and once flying: `position` {x, y, z} in metres from the docking port (x is the distance along the docking axis and must reach 0; y is left/right; z is up/down), `range` in metres, and `attitude` {roll, pitch, yaw} in degrees (all zero means pointing straight at the port). Velocities and rotation rates are **not** reported; estimate them from successive readings.
>
> **Commands:** `POST BASE/api/command` with JSON `{"command": "<name>", ...}`, or a JSON list of these to run in order. Each response includes the state right after the command (a list of results for a list).
> - `T01` to `T14`: thrusters, with optional `"count"` 1 to 10 (repeat the pulse). What each does is unknown. Each pulse changes a velocity or a rotation rate, and the change persists (there's no friction) until cancelled. Thrusters differ in strength, and some may do nothing. When attitude is zero, the vehicle's axes line up with x/y/z; when it's tilted, translation pulses push along the tilted axes.
> - `reset`: start a new attempt from the start point (about 200 m out, offset, and tilted). Needed once at the beginning. The mission clock keeps running across attempts.
> - `pause` / `resume`: freeze or unfreeze time. `step` with `"seconds"` (up to 60): while paused, advance exactly that much sim time. Thruster commands work while paused and take effect on the next step.
>
> **Docking:** reach `range` < 0.2 m with roll, pitch and yaw each within 0.2° and speed on each axis under 0.24 m/s. You fail if you touch the station anywhere else, drift beyond 500 m, or break the **approach corridor**: your closing speed must never exceed 0.1 + 0.02 × range m/s. Gravity causes a very slow downward drift.
>
> Only simulated time counts, not wall-clock time, so pausing to think is free. Every second of `step` counts, including exploration.

### Rules

- Your **score is `mission_time_s`**: total sim time from your first `reset` until you dock, across every attempt.
- **Only your agent flies.** Manual controls are disabled on the Mission Control page. Don't use your browser's developer tools on that tab, and don't read its network traffic: the browser has to know the real thruster names, so peeking is the one way to cheat. The scramble is random per mission and never written anywhere else, so reading the server's source code won't help.
- One team, one leaderboard name. Start as many missions as you like.

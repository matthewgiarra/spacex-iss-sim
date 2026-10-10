# Running the Dragon Docking hackathon challenge

This folder turns the simulator into a hosted, password-protected hackathon booth with three AI challenges and a leaderboard for a big screen. Participants need only a browser for the first two challenges. Your Anthropic API key stays on the server and never reaches their browsers.

| Challenge | Who it's for | What participants need |
|---|---|---|
| **Beginner: AI CAPCOM.** You fly; an AI capsule communicator talks you in and remembers your previous attempts. Pick the model, or fly solo. | Anyone | A browser |
| **Intermediate: Role Reversal.** An AI pilot flies; you're its CAPCOM. You get 10 radio calls per flight, plus standing orders you refine between flights. | Anyone who has tried the beginner tier | A browser |
| **Advanced: Black Box Dragon.** Your own AI agent flies over HTTP, but the thrusters are unlabeled and scrambled, and the sensors report only position and attitude. | People comfortable with an AI coding agent | An AI agent plus Python or PowerShell |

Participant instructions, the handout you share before the event, are in **[CHALLENGES.md](CHALLENGES.md)**.

Pages on the hosted site:

| URL | What it is |
|---|---|
| `/` | Challenge picker (after the password) |
| `/leaderboard` | Leaderboard for the booth TV: overall plus one board per tier, refreshing every 5 s |
| `/admin` | Lists every run; delete test runs or cheats. Log in with the admin password. |

---

## 1. Try it on your own computer (5 minutes)

You need Python 3.10 or newer.

```bash
pip install -r hackathon/requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...        # Windows PowerShell: $env:ANTHROPIC_API_KEY="sk-ant-..."
export HACKATHON_PASSWORD=letmein
export ADMIN_PASSWORD=staffonly
python hackathon/app.py
```

Open http://localhost:8000, type `letmein`, and try each challenge. Open http://localhost:8000/leaderboard in another window to see results arrive.

Without `ANTHROPIC_API_KEY` everything except the AI still works: solo flying, Black Box, and the leaderboard.

---

## 2. Set up your Anthropic account

The server calls the Claude API with your key, so every CAPCOM message and AI-pilot decision is billed to your account. These steps keep that from running out mid-event or running away.

1. **Create an account** at [console.anthropic.com](https://console.anthropic.com) and add a payment method under **Settings → Billing**.
2. **Buy credits** under **Settings → Billing**. API usage is prepaid.
3. **Turn on auto-reload** under **Settings → Billing**. Set a threshold and a reload amount, for example "when the balance drops below $25, add $50". Then a busy afternoon tops itself up instead of stopping. (Menu names may drift as the Console changes; look for "auto-reload" on the billing page.)
4. **Make a dedicated workspace with a spend limit.** Under **Settings → Workspaces**, create a workspace called `hackathon` and set its monthly spend limit to the most you're willing to spend. Auto-reload keeps the account funded; the workspace limit caps what this event can use.
5. **Create an API key in that workspace** (**Settings → API keys**, choose the `hackathon` workspace). This is the `ANTHROPIC_API_KEY` you give the server. Revoke it after the event.
6. **Check your rate limits** under **Settings → Limits**. A busy booth sends a lot of small requests: each beginner player's CAPCOM speaks every few seconds, and each AI pilot makes a decision every few seconds. New accounts start at a low usage tier. If your requests-per-minute limit looks tight for the number of laptops at your booth, buying more credits up front usually moves the account up a tier. Do it a few days before the event.

The server has its own guard too: `DAILY_BUDGET_USD` (default $50) stops all AI calls once the day's estimated spend reaches it. Participants see "today's AI budget is used up; ask the booth staff". Raise it and restart if you need to.

### What it costs (rough estimates)

Each CAPCOM call or pilot decision sends about 2,000 to 3,000 tokens and gets a short reply. Approximate cost per attempt at current prices:

| Model | Beginner run (~5 min, a call every ~5 s) | Intermediate flight (~100 to 200 pilot decisions) |
|---|---|---|
| Claude Haiku 5.5 | about $0.03 | about $0.10 |
| Claude Sonnet 5.5 | about $0.60 | about $1 to $2 |
| Claude Opus 5.5 | about $1.20 | about $2 to $4 |

Haiku is the default choice in the menus. To offer fewer models, remove them from `MODELS` in [`claude.py`](claude.py).

---

## 3. Put it online

Any host that runs a long-lived Python process with a small persistent disk will work. These steps use [Render](https://render.com) because it can deploy straight from your GitHub repository with no server administration.

1. Push this repository to GitHub.
2. In Render, choose **New → Blueprint** and select the repository. Render reads [`render.yaml`](../render.yaml), which defines one web service with a 1 GB disk for the leaderboard.
3. Render asks for the three secrets:
   - `ANTHROPIC_API_KEY`: the key from step 2.5 above.
   - `HACKATHON_PASSWORD`: what participants type. Make it easy to say out loud but not guessable, like `orbital-pancake-42`.
   - `ADMIN_PASSWORD`: for you and your staff. It also works as a participant password.
4. Click **Apply**. The first deploy takes a few minutes. Your site is at `https://dragon-docking.onrender.com` (or similar). Check that `https://<your-site>/healthz` says `{"ok": true}`.
5. Log in, fly a test run in each tier, then delete the test runs at `/admin`.

Notes:

- The blueprint uses Render's **Starter** plan (a few dollars a month) because the free plan sleeps when idle and can't keep a disk, so the leaderboard would be wiped on every restart. Delete the service after the event.
- Run **one instance only**. Flights in progress live in the server's memory.
- To change a setting later, edit the environment variables on the service's **Environment** tab. Render restarts it automatically. Restarting forgets flights in progress but keeps the leaderboard and CAPCOM memories.
- Hosting elsewhere: run `pip install -r hackathon/requirements.txt` and `python hackathon/app.py` with the environment variables below, behind HTTPS. The server listens on `$PORT` (default 8000).

### Settings

| Variable | Required | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | for the AI | Your Anthropic API key |
| `HACKATHON_PASSWORD` | yes | The participant password |
| `ADMIN_PASSWORD` | recommended | Staff password for `/admin` |
| `SESSION_SECRET` | recommended | Long random string. Keeps people logged in across restarts (the blueprint generates one). |
| `DAILY_BUDGET_USD` | no | Stop AI calls when today's estimated spend (UTC day) reaches this. Default 50. |
| `DATA_FILE` | no | Where the leaderboard and memories are saved. Default `hackathon/data/hackathon.json`. |
| `LEADERBOARD_PUBLIC` | no | `1` lets `/leaderboard` be viewed without the password, handy for the TV. Default off. |
| `PORT` | no | Port to listen on. Default 8000; Render sets it for you. |

---

## 4. At the event

- **TV:** open `https://<your-site>/leaderboard` in a full-screen browser (F11). It shows the site address so passers-by know where to play, and new records flash.
- **Password:** put it on a sign at the booth. Change it after the event, or delete the service.
- **Laptops:** any recent Chrome, Edge or Firefox with graphics acceleration works. Players must keep the simulator tab visible, because browsers pause hidden tabs.
- **Cheating:** times are measured in the player's browser, so someone determined could fake one with developer tools. The server rejects implausible times, and for the AI-pilot tier it checks the reported time against its own flight log. For anything that looks too good, check `/admin`, which shows every run, and delete it.
- **Spend:** watch the Usage page in the Anthropic Console during the day.
- **Fresh leaderboard for a new event:** stop the server and delete the data file (on Render, open the **Shell** tab and run `rm /var/data/hackathon.json`, then restart).

---

## How it works

```
 participant's browser                                   hackathon/app.py (your server)              Anthropic API
 ┌──────────────────────────────┐   telemetry, chat    ┌──────────────────────────────┐   prompts   ┌───────────┐
 │ simulator (runs here)        │ ───────────────────▶ │ password check (cookie)      │ ──────────▶ │  Claude   │
 │ js/hackathon.js side panel   │ ◀─────────────────── │ CAPCOM / pilot prompts       │ ◀────────── │           │
 └──────────────────────────────┘   replies, commands  │ leaderboard + memory (JSON)  │             └───────────┘
                                                       │ Black Box relay + scrambler  │ ◀── participant's agent (HTTP)
                                                       └──────────────────────────────┘
```

- **The simulator still runs in the browser.** [`js/hackathon.js`](../iss-sim.spacex.com/js/hackathon.js) adds the side panel when the page is opened with `?mode=beginner|intermediate|advanced`. It also enforces the approach-corridor rule after every physics step, and blocks the manual controls in tiers where the AI or an agent flies. Without `?mode=`, for example under the local `server.py`, the simulator is unchanged.
- **CAPCOM** gets telemetry about once a second from the browser and is called every ~12 s, or sooner when something happens: a milestone, a warning, or a message from the player. The browser computes sign-correct guidance ("Y offset +3.2 m: translate left (A)"), so the model coaches instead of doing coordinate maths. After each attempt CAPCOM writes a debrief, which is stored under the player's callsign and shown to CAPCOM at the start of the next attempt. That's its memory.
- **The AI pilot** makes one decision per turn through a `fly` tool (thruster pulses plus how long to coast), with the sim paused while it thinks. Each turn it sees the player's standing orders, every radio call this flight (unseen ones marked NEW), its last few turns, and the current telemetry. Prompts are rebuilt every call, so cost per decision stays flat.
- **Black Box** missions get a random, private scramble: the 12 real thrusters plus 2 dead ones, renamed T01 to T14 in random order, each multiplied 1×, 2× or 3×. The mapping never leaves the server. The agent talks to `/bb/<mission id>/api/...`, the server translates and relays the commands to that mission's browser tab (the same relay as `server.py`), and it reports only position, range and attitude. The mission clock adds up the sim time of every attempt, and the server records the docking itself.
- Prompts live in [`claude.py`](claude.py). The CAPCOM, Opus and Sonnet calls use the server-side refusal fallback (`fallbacks: "default"`), so a rare false-positive safety refusal is retried on a fallback model instead of going silent.

### Tuning the challenges

- **AI pilot difficulty.** The intermediate tier only works if the pilot is beatable but coachable. Playtest it with a few models before the event. If it docks easily with no help, give it less information: trim `PILOT_SYSTEM` in `claude.py`, or offer only the smallest model. If it never gets close even with good coaching, add a hint or two to `PILOT_SYSTEM`.
- **Radio calls per flight:** `PILOT_MESSAGE_BUDGET` in `app.py`.
- **Corridor rule:** `corridorLimit()` in `js/hackathon.js` (closing speed under 0.1 + 0.02 × range m/s). It applies to the intermediate and advanced tiers; the beginner tier has only the simulator's own docking limits.
- **Black Box:** `BLACKBOX_DEAD_THRUSTERS` and the 1×/2×/3× multipliers in `new_mission()` in `app.py`; the sensor fields agents get are in `AGENT_STATE_KEYS`.

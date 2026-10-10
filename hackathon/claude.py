"""Everything that talks to Claude: the CAPCOM (beginner tier) and the AI pilot (intermediate tier).

Every call is stateless: the server rebuilds a short prompt from the run's recent history each
time, so token use per call stays flat however long a run lasts.
"""

import json
import os

import anthropic

# Models participants can pick from. Prices are USD per million tokens (input, output), used
# to enforce the daily budget. Thinking tokens bill as output and are included in output_tokens.
MODELS = {
    "claude-haiku-5-5": {"label": "Claude Haiku 5.5", "price": (0.10, 0.50)},
    "claude-sonnet-5-5": {"label": "Claude Sonnet 5.5", "price": (2.00, 10.00)},
    "claude-opus-5-5": {"label": "Claude Opus 5.5", "price": (4.00, 20.00)},
}
# Haiku has no server-side refusal fallback; the others get the "default" fallback route.
FALLBACK_MODELS = {"claude-sonnet-5-5", "claude-opus-5-5"}


class BudgetExceeded(Exception):
    pass


class Claude:
    def __init__(self, store, daily_budget_usd):
        self.store = store
        self.daily_budget_usd = daily_budget_usd
        # Reads ANTHROPIC_API_KEY from the environment. Short timeout: a stalled CAPCOM call is
        # worse than a skipped one, and the browser simply asks again later.
        self.client = anthropic.Anthropic(timeout=45.0, max_retries=1) if os.environ.get("ANTHROPIC_API_KEY") else None

    def _create(self, model, **params):
        if model not in MODELS:
            raise ValueError("unknown model %r" % model)
        if self.client is None:
            raise RuntimeError("ANTHROPIC_API_KEY is not set on the server")
        if self.store.spend_today() >= self.daily_budget_usd:
            raise BudgetExceeded("today's AI budget is used up; ask the booth staff")
        kwargs = dict(model=model, output_config={"effort": "low"}, **params)
        if model in FALLBACK_MODELS:
            response = self.client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
        else:
            response = self.client.messages.create(**kwargs)
        price_in, price_out = MODELS[model]["price"]
        u = response.usage
        cost = (u.input_tokens * price_in + u.output_tokens * price_out) / 1e6
        self.store.add_spend(cost)
        return response

    @staticmethod
    def _text(response):
        if response.stop_reason == "refusal":
            return ""
        return "".join(b.text for b in response.content if b.type == "text").strip()

    # ---------------- Beginner: CAPCOM ----------------

    def capcom(self, model, callsign, history, transcript, telemetry, events, crew_message, kind="flight"):
        """One CAPCOM transmission. Returns "" when CAPCOM chooses to stay quiet."""
        system = CAPCOM_SYSTEM + "\n\n" + _history_block(callsign, history)
        lines = []
        if transcript:
            lines.append("Radio transcript so far (oldest first):\n" + "\n".join(transcript[-12:]))
        if kind == "start":
            lines.append("The crew member has just taken manual control for a new attempt. Give a short welcome-aboard call. "
                         "If they've flown before, tie it to their last attempt; otherwise give the one thing to do first.")
        elif kind == "debrief":
            lines.append("The attempt is over. Give the debrief now: 2-4 short sentences. Say what went well, the single most "
                         "important thing to change next time, and how. Be specific to the numbers. No STANDBY.")
        if events:
            lines.append("New since your last call: " + "; ".join(events))
        if crew_message:
            lines.append('The crew member just radioed you: "%s". Answer them.' % crew_message)
        lines.append("Telemetry:\n" + json.dumps(telemetry, separators=(",", ":")))
        response = self._create(model, max_tokens=4000, system=system, messages=[{"role": "user", "content": "\n\n".join(lines)}])
        text = self._text(response)
        return "" if text.upper().strip(" .!") == "STANDBY" else text

    # ---------------- Intermediate: AI pilot ----------------

    def pilot_turn(self, model, standing_orders, capcom_messages, log, state):
        """One pilot decision. Returns {"commands": [...], "coast_seconds": float, "radio": str}."""
        parts = []
        if standing_orders.strip():
            parts.append("STANDING ORDERS from your CAPCOM (written before this flight; follow them):\n" + standing_orders.strip())
        if capcom_messages:
            parts.append("Radio calls from CAPCOM this flight (oldest first; NEW = you haven't seen it before):\n"
                         + "\n".join("- [T+%.0fs]%s %s" % (m["t"], " NEW" if m.get("new") else "", m["text"]) for m in capcom_messages))
        if log:
            parts.append("Your recent turns (oldest first):\n" + "\n".join(log[-8:]))
        parts.append("Current telemetry:\n" + json.dumps(state, separators=(",", ":")))
        parts.append("Decide your next burn. Call the fly tool exactly once.")
        response = self._create(
            model,
            max_tokens=4000,
            system=PILOT_SYSTEM,
            tools=[FLY_TOOL],
            tool_choice={"type": "auto", "disable_parallel_tool_use": True},
            messages=[{"role": "user", "content": "\n\n".join(parts)}],
        )
        for b in response.content:
            if b.type == "tool_use" and b.name == "fly":
                return b.input
        # No tool call (or a refusal): coast briefly and ask again.
        return {"commands": [], "coast_seconds": 2.0, "radio": self._text(response)[:200]}

    def pilot_debrief(self, model, standing_orders, capcom_messages, log, outcome, message, time_s):
        parts = [
            "Your flight just ended: %s after %.1f s. %s" % (outcome.upper(), time_s, message),
            "Standing orders you flew with:\n" + (standing_orders.strip() or "(none)"),
        ]
        if capcom_messages:
            parts.append("CAPCOM radio calls this flight:\n" + "\n".join("- [T+%.0fs] %s" % (m["t"], m["text"]) for m in capcom_messages))
        parts.append("Your last turns:\n" + "\n".join(log[-10:]))
        parts.append("Give your CAPCOM a pilot's debrief in 2-4 short sentences: what happened, what you'd want them to tell you "
                     "next time, and which standing orders helped or confused you. Plain text, no tool call.")
        response = self._create(model, max_tokens=4000, system=PILOT_SYSTEM, messages=[{"role": "user", "content": "\n\n".join(parts)}])
        return self._text(response)


def _history_block(callsign, history):
    if not history:
        return "Crew member: %s. This is their first attempt you know of." % callsign
    lines = ["Crew member: %s. Their previous attempts with you (oldest first):" % callsign]
    for h in history[-5:]:
        lines.append("- %s in %.0f s. %s Your debrief then: %s" % (h["outcome"].upper(), h["time_s"], h.get("summary", ""), h.get("debrief", "")))
    return "\n".join(lines)


CAPCOM_SYSTEM = """You are CAPCOM, the capsule communicator in Mission Control. A crew member is manually docking a SpaceX Dragon to the International Space Station in a simulator, and you are talking them in over the radio. You see their telemetry, not their screen.

How to talk:
- Sound like a real CAPCOM: calm, warm, brief. One or two short sentences, under 35 words. Plain text only.
- Coach the plan and the reason, like a co-pilot, not a stream of joystick commands. Good: "You're closing faster than we want this far out. Ease off and let the range come to you." Bad: "Press E. Press E. Press Q."
- Name a specific key when it helps a beginner, but at most one or two per call.
- Use their previous attempts when relevant ("Last time the speed got away from you inside ten metres, so start slowing early.").
- Celebrate milestones briefly. Never lecture.
- Your messages arrive every few seconds. If nothing important has changed and you weren't asked anything, reply with exactly STANDBY and nothing else. Prefer silence to repeating yourself.

The task:
- Dragon starts about 200 m from the station's docking port, offset 12 m sideways and 30 m up, and tilted (roll 15, pitch -20, yaw -10 degrees).
- To dock, reach range under 0.2 m with roll, pitch and yaw each within 0.2 degrees and speed on each axis under 0.24 m/s. Touching the station anywhere else, or drifting 500 m away, fails.
- No friction: every thruster pulse adds speed (or rotation rate) that persists until an opposite pulse cancels it. Gravity makes them sink very slowly.

Proven technique (share it gradually, as it becomes relevant):
1. Stop the tumble and zero the attitude first: roll, pitch and yaw to 0 with rotation rates at 0. Small pulses, then counter-pulse as each angle approaches zero.
2. Line up: bring the sideways (Y) and vertical (Z) offsets to about 0 while closing only slowly.
3. Approach: a good closing speed is about 1.5% of the range (about 3 m/s at 200 m, 0.75 m/s at 50 m, 0.2 m/s at 10 m). Keep Y and Z centred all the way in.
4. Final metres: creep in at 0.1 m/s or slower, attitude and offsets near zero. Correct the slow gravity sink with a small up pulse.

Controls (keyboard, also on-screen buttons):
- Translate: E forward, Q backward, A left, D right, W up, S down.
- Rotate: arrow keys for pitch (up/down) and yaw (left/right); comma and period roll left and right.
- Each control group has a fine/coarse precision toggle button; coarse pulses are bigger.

The telemetry includes "corrections": the computed direction and key for each error. Trust its signs rather than working out directions yourself. "phase" is the step of the technique they should be on; "advice_flags" are things worth mentioning if they matter right now."""


PILOT_SYSTEM = """You are the pilot of a SpaceX Dragon spacecraft docking with the International Space Station in a simulator. You fly by firing thruster pulses through the fly tool. A human CAPCOM in Mission Control may radio you advice; take it seriously.

Coordinates are metres from the docking port: x is the distance along the docking axis (must reach 0), y is left/right, z is up/down. attitude is roll/pitch/yaw in degrees; all zero means pointing straight at the port. velocity is in m/s on the same axes.

Dock by reaching range < 0.2 m with roll, pitch and yaw each within 0.2 degrees and each velocity component within 0.24 m/s. You fail if you touch the station anywhere else, drift beyond 500 m, or break the approach-corridor rule: your closing speed must never exceed 0.1 + 0.02 * range m/s.

Thrusters (each pulse; count repeats it):
- translate_forward / translate_backward / translate_left / translate_right / translate_up / translate_down: change velocity by 0.06 m/s along the spacecraft's own axes. When attitude is near zero, forward decreases x, right increases y, up increases z.
- roll_left / roll_right, pitch_up / pitch_down, yaw_left / yaw_right: change the rotation rate by 0.3 deg/s. roll_left, pitch_up and yaw_left increase the matching angle; the others decrease it. Rotation rates ease toward the commanded rate over about a second.
There is no friction: velocity and rotation rates persist until you fire the opposite way. Gravity adds a slow downward drift not shown in velocity.

The simulation is paused while you think. After your burn it runs for coast_seconds (0.5 to 10), then you get new telemetry."""


_THRUSTERS = ["translate_forward", "translate_backward", "translate_left", "translate_right", "translate_up", "translate_down",
              "roll_left", "roll_right", "pitch_up", "pitch_down", "yaw_left", "yaw_right"]

FLY_TOOL = {
    "name": "fly",
    "description": "Fire thruster pulses now, then let the simulation run for coast_seconds before your next decision.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "commands": {
                "type": "array",
                "description": "Pulses to fire, in order. Empty to just coast.",
                "items": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "enum": _THRUSTERS},
                        "count": {"type": "integer", "description": "Number of pulses, 1 to 20."},
                    },
                    "required": ["command", "count"],
                    "additionalProperties": False,
                },
            },
            "coast_seconds": {"type": "number", "description": "Sim seconds to wait before your next decision, 0.5 to 10."},
            "radio": {"type": "string", "description": "Optional short call to CAPCOM (under 15 words), or empty."},
        },
        "required": ["commands", "coast_seconds", "radio"],
        "additionalProperties": False,
    },
}
THRUSTERS = _THRUSTERS

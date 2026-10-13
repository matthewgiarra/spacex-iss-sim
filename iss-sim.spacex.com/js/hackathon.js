// Hackathon modes for the docking simulator. Active only when the page is served by
// hackathon/app.py and opened with ?mode=beginner|intermediate|advanced; otherwise it does nothing.
//
//   beginner      you fly; an AI CAPCOM watches your telemetry and talks you in
//   intermediate  your own autopilot program flies in real time: it requests telemetry and sends thruster inputs
//   advanced      Black Box: your own agent flies over HTTP with unlabeled thrusters
(function () {
    "use strict";

    var MODE = new URLSearchParams(location.search).get("mode");
    var TIERS = {
        beginner: "BEGINNER · AI CAPCOM",
        intermediate: "INTERMEDIATE · AUTOPILOT",
        advanced: "ADVANCED · BLACK BOX DRAGON",
    };
    if (!TIERS[MODE]) return;

    window.dragonBridge = { url: null }; // no local server.py bridge on the hosted site
    var PANEL_W = 380;
    var CORRIDOR = MODE !== "beginner"; // closing speed must stay under 0.1 + 0.02 * range
    var MANUAL_FLIGHT = MODE === "beginner";
    var cfg = null;
    var toDeg = 180 / Math.PI;

    // ------------------------------------------------------------------ utilities

    function el(tag, attrs, children) {
        var e = document.createElement(tag);
        Object.keys(attrs || {}).forEach(function (k) {
            if (k === "text") e.textContent = attrs[k];
            else if (k === "html") e.innerHTML = attrs[k];
            else if (k.slice(0, 2) === "on") e.addEventListener(k.slice(2), attrs[k]);
            else e.setAttribute(k, attrs[k]);
        });
        (children || []).forEach(function (c) { if (c) e.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
        return e;
    }

    function store(key, value) {
        try {
            if (value === undefined) return localStorage.getItem(key);
            localStorage.setItem(key, value);
        } catch (e) { return null; }
    }

    function api(path, body) {
        return fetch(path, {
            method: body === undefined ? "GET" : "POST",
            headers: { "Content-Type": "application/json" },
            body: body === undefined ? undefined : JSON.stringify(body),
            credentials: "same-origin",
        }).then(function (r) {
            return r.json().catch(function () { return {}; }).then(function (j) {
                if (!r.ok) {
                    var err = new Error(j.error || "HTTP " + r.status);
                    err.status = r.status;
                    throw err;
                }
                return j;
            });
        });
    }

    function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
    function fmt(n, d) { return (n >= 0 ? "+" : "") + n.toFixed(d === undefined ? 1 : d); }
    function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
    function fmtTime(s) {
        s = Math.max(0, s);
        var m = Math.floor(s / 60);
        return m + ":" + ("0" + (s - 60 * m).toFixed(1)).slice(-4);
    }

    // ------------------------------------------------------------------ physics hooks

    function rel() {
        var p = { x: camera.position.z - issObject.position.z, y: camera.position.x - issObject.position.x, z: camera.position.y - issObject.position.y };
        var v = { x: motionVector.z * PHYSICS_HZ, y: motionVector.x * PHYSICS_HZ, z: motionVector.y * PHYSICS_HZ };
        var range = Math.sqrt(p.x * p.x + p.y * p.y + p.z * p.z);
        var closing = range > 0 ? -(p.x * v.x + p.y * v.y + p.z * v.z) / range : 0;
        return { p: p, v: v, range: range, closing: closing };
    }

    function corridorLimit(range) { return 0.1 + 0.02 * range; }

    // Check the approach corridor after every physics step, so long 'step' calls can't skip it.
    var originalPhysicsStep = window.physicsStep;
    window.physicsStep = function () {
        originalPhysicsStep();
        if (!CORRIDOR || isGameOver || !isWarpComplete) return;
        var r = rel();
        if (r.closing > corridorLimit(r.range)) {
            $("#fail-message").innerHTML =
                "You broke the approach corridor: closing at " + r.closing.toFixed(2) + " m/s with " + r.range.toFixed(1) +
                " m to go (limit " + corridorLimit(r.range).toFixed(2) + " m/s).";
            window.hideInterface("fail");
        }
    };

    // Keyboard: keep typing in the panel away from the flight controls (see buildPanel), block
    // the debug teleport code (digits), and block manual flying where the AI or an agent flies.
    function guardKeys(e) {
        if (panel && panel.contains(e.target)) return; // reaches the panel, which stops it there
        var digit = e.keyCode >= 48 && e.keyCode <= 57;
        if (digit || !MANUAL_FLIGHT) e.stopPropagation();
    }
    window.addEventListener("keydown", guardKeys, true);
    window.addEventListener("keyup", guardKeys, true);

    // ------------------------------------------------------------------ layout: the sim gets the left part of the window

    var panel, logBox, statusBox, timerBox, setupBox, inputForm, inputBox, gaugeBox;

    function fitSim() {
        var w = Math.max(320, window.innerWidth - PANEL_W), h = window.innerHeight;
        width = w; height = h; windowHalfX = w / 2; windowHalfY = h / 2;
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
        renderer.setSize(w, h);
        tooltipRenderer.setSize(w, h);
    }

    function buildPanel() {
        document.documentElement.classList.add("hk", "hk-" + MODE);
        if (!MANUAL_FLIGHT) document.documentElement.classList.add("hk-noflight");
        logBox = el("div", { class: "hk-log" });
        statusBox = el("div", { class: "hk-status" });
        timerBox = el("div", { class: "hk-timer", text: "0:00.0" });
        gaugeBox = el("div", { class: "hk-gauge" });
        setupBox = el("div", { class: "hk-setup" });
        inputBox = el("input", { type: "text", maxlength: "300", placeholder: "" });
        inputForm = el("form", { class: "hk-input" }, [inputBox, el("button", { type: "submit", text: "SEND" })]);
        panel = el("div", { id: "hk-panel" }, [
            el("div", { class: "hk-head" }, [
                el("a", {
                    href: "/", text: "◂ CHALLENGES",
                    onclick: function (e) { if (!confirm("End simulation and return to the challenge page?")) e.preventDefault(); },
                }),
                el("a", { href: "/leaderboard", target: "_blank", text: "LEADERBOARD ▸" }),
            ]),
            el("div", { class: "hk-tier", text: TIERS[MODE] }),
            setupBox,
            el("div", { class: "hk-row" }, [timerBox, statusBox]),
            gaugeBox,
            logBox,
            inputForm,
        ]);
        // The sim listens on document for flight keys, and for mouse presses that it preventDefault()s
        // (which would stop clicks from focusing the panel's inputs). Keep panel events to the panel.
        ["keydown", "keyup", "keypress", "mousedown", "mouseup", "mousemove", "touchstart", "touchend"].forEach(function (t) {
            panel.addEventListener(t, function (e) { e.stopPropagation(); });
        });
        // Give the keyboard back to the sim: Esc leaves a panel input, and so does clicking the sim
        // (its own mousedown handler preventDefault()s, which would otherwise keep the input focused).
        panel.addEventListener("keydown", function (e) { if (e.key === "Escape") e.target.blur(); });
        document.addEventListener("mousedown", function (e) {
            var a = document.activeElement;
            if (a && a !== document.body && panel.contains(a) && !panel.contains(e.target)) a.blur();
        }, true);
        document.body.appendChild(panel);
        fitSim();
        window.addEventListener("resize", fitSim);
    }

    function log(kind, text) {
        var who = { capcom: "CAPCOM", you: "YOU", sys: "", debrief: "DEBRIEF" }[kind];
        var line = el("div", { class: "hk-msg hk-" + kind }, [who ? el("b", { text: who + " " }) : null, text]);
        logBox.appendChild(line);
        logBox.scrollTop = logBox.scrollHeight;
        if (voiceOn && (kind === "capcom" || kind === "debrief") && window.speechSynthesis) {
            window.speechSynthesis.cancel();
            window.speechSynthesis.speak(new SpeechSynthesisUtterance(text));
        }
        return line;
    }

    function setStatus(text) { statusBox.textContent = text; }

    var voiceOn = store("hk_voice") === "1";

    function field(label, input) { return el("label", { class: "hk-field" }, [el("span", { text: label }), input]); }

    function callsignInput() {
        var input = el("input", { type: "text", maxlength: "24", placeholder: "e.g. Maverick", value: store("hk_callsign") || "" });
        input.addEventListener("input", function () {
            store("hk_callsign", input.value.trim());
            updateLock();
        });
        return input;
    }

    function modelSelect(key, allowNone, fallback) {
        var sel = el("select");
        if (allowNone) sel.appendChild(el("option", { value: "none", text: "None (fly solo)" }));
        cfg.models.forEach(function (m) { sel.appendChild(el("option", { value: m.id, text: m.label })); });
        sel.value = store(key) || fallback;
        if (!sel.value) sel.value = fallback;
        sel.addEventListener("change", function () { store(key, sel.value); });
        return sel;
    }

    function voiceToggle() {
        var box = el("input", { type: "checkbox" });
        box.checked = voiceOn;
        box.addEventListener("change", function () {
            voiceOn = box.checked;
            store("hk_voice", voiceOn ? "1" : "0");
            if (!voiceOn && window.speechSynthesis) window.speechSynthesis.cancel();
        });
        return el("label", { class: "hk-check" }, [box, " Read radio calls aloud"]);
    }

    function callsign() { return (store("hk_callsign") || "").trim(); }

    function updateLock() {
        document.documentElement.classList.toggle("hk-locked", !callsign());
    }

    function updateGauge() {
        if (!isWarpComplete || isGameOver) return (gaugeBox.textContent = "");
        var r = rel();
        if (CORRIDOR) {
            var lim = corridorLimit(r.range), frac = clamp(r.closing / lim, 0, 1);
            gaugeBox.innerHTML = "";
            gaugeBox.appendChild(el("div", { class: "hk-gauge-label", text: "CLOSING " + r.closing.toFixed(2) + " / LIMIT " + lim.toFixed(2) + " m/s" }));
            var bar = el("div", { class: "hk-bar" }, [el("i", { style: "width:" + (100 * frac).toFixed(1) + "%" })]);
            if (frac > 0.85) bar.classList.add("hk-warn");
            gaugeBox.appendChild(bar);
        } else {
            gaugeBox.textContent = "RANGE " + r.range.toFixed(1) + " m · CLOSING " + r.closing.toFixed(2) + " m/s";
        }
    }

    // ================================================================== BEGINNER: AI CAPCOM

    var beginner = {
        run: null, inflight: false, lastCall: 0, queue: [], events: [], samples: [], flags: {}, lastTime: 0,
        stats: null, wasFlying: false,
    };

    // What the CAPCOM sees: telemetry plus pre-computed, sign-correct guidance, so the model
    // talks about the flight instead of doing coordinate math.
    function capcomTelemetry() {
        var s = dragon.getState();
        if (!s.position) return { status: s.status };
        var r = rel();
        var att = s.attitude, rate = s.rotation_rate, p = s.position, v = s.velocity;
        var maxAtt = Math.max(Math.abs(att.roll), Math.abs(att.pitch), Math.abs(att.yaw));
        var maxRate = Math.max(Math.abs(rate.roll), Math.abs(rate.pitch), Math.abs(rate.yaw));
        var lateral = Math.max(Math.abs(p.y), Math.abs(p.z));
        var phase = maxAtt > 1 || maxRate > 0.2 ? "1 ZERO ATTITUDE" : lateral > 1 && p.x > 10 ? "2 LINE UP" : r.range > 10 ? "3 APPROACH" : "4 FINAL";
        var rec = clamp(0.015 * r.range, 0.08, 3);

        var corrections = [];
        // [axis, value, rate, key that decreases it, key that increases it]
        [["roll", att.roll, rate.roll, "roll right (period key)", "roll left (comma key)"],
         ["pitch", att.pitch, rate.pitch, "pitch down (down arrow)", "pitch up (up arrow)"],
         ["yaw", att.yaw, rate.yaw, "yaw right (right arrow)", "yaw left (left arrow)"]].forEach(function (a) {
            var val = a[1], rt = a[2];
            if (Math.abs(val) <= 0.2 && Math.abs(rt) < 0.05) return;
            var towardZero = val * rt < 0;
            if (Math.abs(val) <= 0.2) corrections.push(a[0] + " is centred but still rotating " + fmt(rt, 2) + " deg/s: stop it with " + (rt > 0 ? a[3] : a[4]));
            else if (towardZero) corrections.push(a[0] + " " + fmt(val) + " deg, coming back toward 0 at " + Math.abs(rt).toFixed(2) + " deg/s: good, counter with " + (rt > 0 ? a[3] : a[4]) + " as it nears 0");
            else corrections.push(a[0] + " " + fmt(val) + " deg" + (Math.abs(rt) >= 0.05 ? " and moving away from 0" : "") + ": " + (val > 0 ? a[3] : a[4]));
        });
        [["Y (sideways)", p.y, v.y, "translate left (A)", "translate right (D)"],
         ["Z (vertical)", p.z, v.z, "translate down (S)", "translate up (W)"]].forEach(function (a) {
            var val = a[1], vel = a[2];
            if (Math.abs(val) <= 0.15 && Math.abs(vel) < 0.03) return;
            var towardZero = val * vel < 0;
            if (Math.abs(val) <= 0.15) corrections.push(a[0] + " is centred but drifting " + fmt(vel, 2) + " m/s: stop it with " + (vel > 0 ? a[3] : a[4]));
            else if (towardZero) corrections.push(a[0] + " offset " + fmt(val) + " m, closing at " + Math.abs(vel).toFixed(2) + " m/s: good, stop with " + (vel > 0 ? a[3] : a[4]) + " near 0");
            else corrections.push(a[0] + " offset " + fmt(val) + " m: " + (val > 0 ? a[3] : a[4]));
        });
        if (r.closing > rec * 1.6) corrections.push("closing " + r.closing.toFixed(2) + " m/s is too fast for " + r.range.toFixed(0) + " m (want about " + rec.toFixed(2) + "): slow with translate backward (Q)");
        else if (phase !== "1 ZERO ATTITUDE" && r.closing < rec * 0.4 && r.range > 3) corrections.push("closing " + r.closing.toFixed(2) + " m/s is slow for " + r.range.toFixed(0) + " m (want about " + rec.toFixed(2) + "): translate forward (E)");

        var flags = [];
        if (phase === "1 ZERO ATTITUDE" && r.closing > 0.3) flags.push("closing on the station before the attitude is fixed");
        if (r.closing < -0.05) flags.push("moving away from the station");
        if (r.range < 5 && (Math.abs(v.x) > 0.24 || Math.abs(v.y) > 0.24 || Math.abs(v.z) > 0.24)) flags.push("too fast for contact: docking limit is 0.24 m/s per axis");
        if (r.range < 5 && maxAtt > 0.2) flags.push("attitude must be within 0.2 deg at contact");
        if (r.range < 20 && lateral > 0.3 * r.range) flags.push("badly off-centre this close: risk of hitting the station");

        // Trend over the last ~10 s
        var trend = null, old = beginner.samples[0];
        if (old && s.time_s - old.t >= 3) {
            trend = { over_s: +(s.time_s - old.t).toFixed(1), range_change_m: +(r.range - old.range).toFixed(2), y_change_m: +(p.y - old.y).toFixed(2), z_change_m: +(p.z - old.z).toFixed(2) };
        }
        return {
            t_s: s.time_s, phase: phase, range_m: +r.range.toFixed(2), closing_mps: +r.closing.toFixed(3), recommended_closing_mps: +rec.toFixed(2),
            position_m: p, velocity_mps: v, attitude_deg: att, rotation_rate_dps: rate, corrections: corrections, advice_flags: flags, trend_last_10s: trend,
        };
    }

    // Notable moments trigger a CAPCOM call even between the regular check-ins.
    function detectEvents(tm) {
        var f = beginner.flags, ev = [];
        if (!tm.phase) return ev;
        if (!f.aligned && tm.phase !== "1 ZERO ATTITUDE") { f.aligned = true; ev.push("attitude is now zeroed and steady"); }
        if (!f.linedUp && (tm.phase === "3 APPROACH" || tm.phase === "4 FINAL")) { f.linedUp = true; ev.push("lined up on the docking axis"); }
        [150, 100, 50, 20, 10, 5, 2, 1].forEach(function (m) {
            if (tm.range_m < m && !f["r" + m]) {
                f["r" + m] = true;
                if (m < 150) ev.push("range just dropped below " + m + " m");
            }
        });
        var now = Date.now();
        tm.advice_flags.forEach(function (flag) {
            if (!f[flag] || now - f[flag] > 15000) { f[flag] = now; ev.push("warning: " + flag); }
        });
        return ev;
    }

    function beginnerStats(tm) {
        var st = beginner.stats;
        if (!tm.phase) return;
        if (tm.range_m < 20) st.maxClosingInside20 = Math.max(st.maxClosingInside20, tm.closing_mps);
        if (st.alignedAt === null && tm.phase !== "1 ZERO ATTITUDE") st.alignedAt = tm.t_s;
        st.minRange = Math.min(st.minRange, tm.range_m);
        st.last = tm;
    }

    function statsSummary() {
        var st = beginner.stats, last = st.last || {};
        var parts = [];
        parts.push(st.alignedAt === null ? "Never got the attitude zeroed." : "Attitude zeroed at T+" + st.alignedAt.toFixed(0) + " s.");
        parts.push("Closest approach " + st.minRange.toFixed(1) + " m.");
        if (st.maxClosingInside20 > 0) parts.push("Max closing speed inside 20 m: " + st.maxClosingInside20.toFixed(2) + " m/s.");
        if (last.velocity_mps) parts.push("At the end: velocity " + JSON.stringify(last.velocity_mps) + " m/s, attitude " + JSON.stringify(last.attitude_deg) + " deg.");
        return parts.join(" ");
    }

    function beginnerTick() {
        var flying = isWarpComplete && !isGameOver;
        var s = dragon.getState();
        timerBox.textContent = fmtTime(beginner.run && flying ? simStepCount / PHYSICS_HZ : beginner.lastTime);
        updateGauge();

        if (flying && !beginner.wasFlying) beginnerStart();
        if (!flying && beginner.wasFlying) beginnerEnd(s);
        beginner.wasFlying = flying;
        if (!flying || !beginner.run) return;

        var tm = capcomTelemetry();
        beginnerStats(tm);
        if (tm.phase) {
            beginner.samples.push({ t: tm.t_s, range: tm.range_m, y: tm.position_m.y, z: tm.position_m.z });
            while (beginner.samples.length && tm.t_s - beginner.samples[0].t > 10) beginner.samples.shift();
        }
        beginner.events = beginner.events.concat(detectEvents(tm));
        if (beginner.run.model === "none" || !beginner.run.id || beginner.inflight) return;
        var crew = beginner.queue.shift();
        var due = Date.now() - beginner.lastCall > 12000;
        if (!crew && !beginner.events.length && !due) return;
        var events = beginner.events;
        beginner.events = [];
        beginner.inflight = true;
        beginner.lastCall = Date.now();
        var run = beginner.run;
        api("/api/beginner/capcom", { run_id: run.id, telemetry: tm, events: events, crew_message: crew || null })
            .then(function (res) { if (res.reply && beginner.run === run) log("capcom", res.reply); })
            .catch(function (e) { if (e.status === 402) log("sys", e.message); })
            .then(function () { beginner.inflight = false; });
    }


    function beginnerStart() {
        simStepCount = 0; // the attempt clock starts when you get control
        var model = beginner.modelSel.value;
        beginner.run = { id: null, model: model };
        beginner.flags = {};
        beginner.samples = [];
        beginner.events = [];
        beginner.queue = [];
        beginner.lastCall = Date.now();
        beginner.stats = { maxClosingInside20: 0, alignedAt: null, minRange: 1e9, last: null };
        beginner.modelSel.disabled = true;
        setStatus(model === "none" ? "Flying solo" : "CAPCOM: " + beginner.modelSel.selectedOptions[0].text);
        var run = beginner.run;
        api("/api/beginner/start", { callsign: callsign(), model: model, telemetry: capcomTelemetry() })
            .then(function (res) {
                run.id = res.run_id;
                if (res.reply) log("capcom", res.reply);
            })
            .catch(function (e) { log("sys", "Couldn't reach Mission Control: " + e.message); });
    }

    function beginnerEnd(s, abort) {
        var run = beginner.run;
        if (!run) return;
        beginner.run = null;
        beginner.lastTime = abort ? 0 : s.time_s;
        beginner.modelSel.disabled = false;
        var outcome = abort ? "abort" : s.status === "success" ? "success" : "fail";
        var t = s.time_s;
        setStatus(outcome === "success" ? "DOCKED in " + fmtTime(t) : outcome === "fail" ? "Attempt failed" : "Restarted");
        if (outcome !== "abort") log("sys", outcome === "success" ? "Docked in " + t.toFixed(1) + " s." : s.message || "Attempt failed.");
        var send = function () {
            if (!run.id) return sleep(500).then(send); // start call still in flight
            if (outcome !== "abort" && run.model !== "none") setStatus("CAPCOM is preparing your debrief…");
            return api("/api/beginner/finish", { run_id: run.id, outcome: outcome, time_s: t, message: s.message, telemetry: beginner.stats.last || {}, stats: { summary: statsSummary() } })
                .then(function (res) {
                    if (res.debrief) log("debrief", res.debrief);
                    if (res.rank) log("sys", "That's #" + res.rank + " on the beginner leaderboard.");
                    if (outcome === "success") setStatus("DOCKED in " + fmtTime(t) + (res.rank ? " · #" + res.rank : ""));
                    else if (outcome === "fail") setStatus("Attempt failed. Press PLAY AGAIN.");
                });
        };
        send().catch(function (e) {
            log("sys", "Couldn't record the run: " + e.message);
            setStatus(outcome === "success" ? "DOCKED in " + fmtTime(t) + " (not recorded)" : "Press PLAY AGAIN");
        });
    }

    function initBeginner() {
        beginner.modelSel = modelSelect("hk_capcom_model", true, cfg.models[0].id);
        setupBox.appendChild(field("CALLSIGN", callsignInput()));
        setupBox.appendChild(el("div", { class: "hk-lock-hint", text: "Type a callsign to unlock BEGIN." }));
        setupBox.appendChild(field("CAPCOM", beginner.modelSel));
        setupBox.appendChild(voiceToggle());
        inputBox.placeholder = "Radio CAPCOM… (Enter sends, then keys fly again)";
        inputForm.addEventListener("submit", function (e) {
            e.preventDefault();
            var text = inputBox.value.trim();
            if (!text) return;
            inputBox.value = "";
            inputBox.blur(); // back to flying: flight keys work again right away
            log("you", text);
            if (!beginner.run || beginner.run.model === "none") return log("sys", "CAPCOM is only on the radio during a flight with a model selected.");
            beginner.queue.push(text);
        });
        // The "restart" option resets mid-flight without ending the attempt; treat it as a new attempt.
        $("#option-restart").addEventListener("click", function () {
            if (!beginner.run) return;
            beginnerEnd(dragon.getState(), true);
            setTimeout(function () { if (!isGameOver) { beginnerStart(); simStepCount = -300; } }, 0); // clock starts after the 5 s reset glide
        });
        updateLock();
        log("sys", "Pick a callsign and a CAPCOM, then press BEGIN. Your fastest docking goes on the leaderboard. CAPCOM remembers your previous attempts.");
        setInterval(beginnerTick, 1000 / 10);
    }

    // ================================================================== INTERMEDIATE: bring your own autopilot
    // The participant's program (any language; the kit has Python, C and C++ clients) sends requests to this
    // site while this tab flies in real time. This panel hands out the program's URL, launches flights and
    // shows results.

    var autopilot = { id: null, seen: 0 };

    function initIntermediate() {
        var cs = callsignInput();
        autopilot.linkBtn = el("button", { class: "hk-btn", type: "button", text: "GET AUTOPILOT LINK", onclick: newAutopilotLink });
        autopilot.launchBtn = el("button", { class: "hk-btn", type: "button", text: "LAUNCH", onclick: launchAutopilot });
        autopilot.launchBtn.style.display = "none";
        autopilot.conn = el("div", { class: "hk-conn" });
        autopilot.box = el("div", { class: "hk-mission" });
        setupBox.appendChild(field("CALLSIGN", cs));
        setupBox.appendChild(el("a", { class: "hk-link", href: "/kit.zip", text: "⬇ Download the starter kit: Python, C and C++ clients, examples, API docs" }));
        setupBox.appendChild(el("div", { class: "hk-buttons" }, [autopilot.linkBtn, autopilot.launchBtn]));
        setupBox.appendChild(autopilot.conn);
        setupBox.appendChild(autopilot.box);
        inputForm.style.display = "none";
        updateLock();
        log("sys", "Your own autopilot program flies this Dragon in real time: it requests telemetry and sends thruster inputs, " +
            "in any language (see AUTOPILOT_API.md in the starter kit). Press GET AUTOPILOT LINK, start your autopilot with that URL, " +
            "then press LAUNCH. " +
            "Rule: closing speed must stay under 0.1 + 0.02 × range m/s. Keep this tab visible while flying.");
        var saved = store("hk_autopilot");
        if (saved) attachAutopilotLink(saved, true);
        setInterval(updateGauge, 100);
    }

    function newAutopilotLink() {
        if (!callsign()) return log("sys", "Pick a callsign first.");
        if (autopilot.id && !confirm("Get a new autopilot link? The old one stops working.")) return;
        api("/api/autopilot", { callsign: callsign() })
            .then(function (res) { attachAutopilotLink(res.id, false); })
            .catch(function (e) { log("sys", e.message); });
    }

    function attachAutopilotLink(id, resumed) {
        api("/api/autopilot/" + encodeURIComponent(id))
            .then(function (st) {
                autopilot.id = id;
                autopilot.seen = st.finished; // don't re-announce old flights
                store("hk_autopilot", id);
                var base = location.origin + "/ap/" + id;
                autopilot.box.innerHTML = "";
                autopilot.box.appendChild(el("div", { class: "hk-label", text: "YOUR AUTOPILOT URL (keep it secret)" }));
                var url = el("input", { type: "text", readonly: "readonly", value: base, class: "hk-url" });
                url.addEventListener("focus", function () { url.select(); });
                autopilot.box.appendChild(url);
                var command = "python autopilot.py " + base;
                autopilot.box.appendChild(el("div", { class: "hk-label", text: "PYTHON EXAMPLE: RUN THIS IN THE STARTER KIT FOLDER" }));
                autopilot.box.appendChild(el("pre", { class: "hk-code", text: command }));
                autopilot.box.appendChild(el("button", {
                    class: "hk-btn hk-btn-ghost", type: "button", text: "COPY COMMAND",
                    onclick: function () { navigator.clipboard && navigator.clipboard.writeText(command); },
                }));
                autopilot.linkBtn.textContent = "NEW LINK";
                autopilot.linkBtn.classList.add("hk-btn-ghost");
                autopilot.launchBtn.style.display = "";
                window.dragonBridge = { url: "/ap/" + id + "/bridge/sync", onSync: function (msg) { showAutopilot(msg.session); } };
                showAutopilot(st);
                if (resumed) log("sys", "Reconnected to your autopilot link.");
            })
            .catch(function () {
                store("hk_autopilot", "");
                if (resumed) log("sys", "Your previous autopilot link has expired. Press GET AUTOPILOT LINK for a new one.");
            });
    }

    function launchAutopilot() {
        if (!autopilot.id) return;
        autopilot.launchBtn.disabled = true;
        api("/api/autopilot/" + encodeURIComponent(autopilot.id) + "/launch", {})
            .then(function () { log("sys", "Launching. The Dragon takes a few seconds to arrive, then your autopilot takes over."); })
            .catch(function (e) { log("sys", "Couldn't launch: " + e.message); autopilot.launchBtn.disabled = false; });
    }

    function showAutopilot(st) {
        if (!st) return;
        autopilot.conn.textContent = st.autopilot_connected ? "● Autopilot connected" : "○ Autopilot not connected";
        autopilot.conn.classList.toggle("hk-ok", !!st.autopilot_connected);
        var f = st.flight, busy = f && (f.status === "launching" || f.status === "flying");
        autopilot.launchBtn.disabled = !!busy;
        if (f && f.status === "launching") setStatus("Launching…");
        else if (f && f.status === "flying") {
            setStatus((st.autopilot_connected ? "Flying · " : "Flying with no autopilot connected · ") + f.commands + " commands");
            if (st.flight_time_s !== null) timerBox.textContent = fmtTime(st.flight_time_s);
        }
        if (f && f.status === "done" && f.result && !isGameOver && isWarpComplete) {
            // The server ended the flight (out of time, aborted): end the attempt on screen too.
            $("#fail-message").textContent = f.result.message;
            window.hideInterface("fail");
        }
        var fresh = Math.min(st.finished - autopilot.seen, st.history.length);
        st.history.slice(st.history.length - fresh).forEach(function (r) {
            if (r.outcome === "success") {
                log("sys", "DOCKED in " + r.time_s.toFixed(1) + " s" + (r.rank ? ", #" + r.rank + " on the autopilot leaderboard." : "."));
                setStatus("Docked in " + fmtTime(r.time_s) + (r.rank ? " · #" + r.rank : ""));
            } else if (r.outcome === "fail") {
                log("sys", "FAILED after " + r.time_s.toFixed(1) + " s: " + r.message);
                setStatus("Failed. Tune your autopilot and LAUNCH again.");
            } else {
                log("sys", "Flight didn't start: " + r.message);
                setStatus("Ready");
            }
            timerBox.textContent = fmtTime(r.time_s);
        });
        autopilot.seen = st.finished;
        if (!f) setStatus(st.autopilot_connected ? "Ready: press LAUNCH" : "Start your autopilot");
    }

    // ================================================================== ADVANCED: Black Box Dragon

    var mission = { id: null };

    function initAdvanced() {
        var cs = callsignInput();
        cs.placeholder = "Team name";
        mission.box = el("div", { class: "hk-mission" });
        mission.startBtn = el("button", { class: "hk-btn", type: "button", text: "START NEW MISSION", onclick: newMission });
        setupBox.appendChild(field("TEAM", cs));
        setupBox.appendChild(el("div", { class: "hk-buttons" }, [mission.startBtn]));
        setupBox.appendChild(mission.box);
        inputForm.style.display = "none";
        updateLock();
        log("sys", "Your agent flies this Dragon over HTTP, but its thrusters are unlabeled (T01-T14) and scrambled per mission. " +
            "Some are stronger than others and some may not work. Its sensors report only position, range and attitude: no velocities or rates. " +
            "The mission clock counts sim time across every attempt until you dock. " +
            "Rule: closing speed must stay under 0.1 + 0.02 × range m/s. Keep this tab visible while your agent flies.");
        var saved = store("hk_mission");
        if (saved) attachMission(saved, true);
        setInterval(updateGauge, 100);
    }

    function newMission() {
        if (!callsign()) return log("sys", "Pick a team name first.");
        if (mission.id && !confirm("Abandon the current mission and start a fresh one? Its clock and scramble are lost.")) return;
        api("/api/blackbox/mission", { callsign: callsign() })
            .then(function (res) {
                logBox.innerHTML = "";
                attachMission(res.mission_id, false);
            })
            .catch(function (e) { log("sys", e.message); });
    }

    function attachMission(id, resumed) {
        api("/api/blackbox/mission/" + encodeURIComponent(id))
            .then(function (st) {
                mission.id = id;
                mission.announced = false;
                store("hk_mission", id);
                var base = location.origin + "/bb/" + id;
                mission.box.innerHTML = "";
                mission.box.appendChild(el("div", { class: "hk-label", text: "YOUR AGENT'S API BASE URL (keep it secret)" }));
                var url = el("input", { type: "text", readonly: "readonly", value: base, class: "hk-url" });
                url.addEventListener("focus", function () { url.select(); });
                mission.box.appendChild(url);
                mission.box.appendChild(el("button", {
                    class: "hk-btn hk-btn-ghost", type: "button", text: "COPY",
                    onclick: function () { navigator.clipboard && navigator.clipboard.writeText(base); },
                }));
                mission.box.appendChild(el("pre", {
                    class: "hk-code",
                    text: "curl " + base + "/api/state\n" +
                        "curl -X POST " + base + "/api/command \\\n  -d '{\"command\": \"reset\"}'\n\n" +
                        "# Python (dragon.py from the repo)\nsim = Dragon(\"" + base + "\")\nsim.reset()\nsim.command(\"T07\", count=2)",
                }));
                window.dragonBridge = { url: "/bb/" + id + "/bridge/sync", onSync: function (msg) { showMission(msg.session); } };
                showMission(st);
                if (resumed) log("sys", "Reconnected to your mission.");
            })
            .catch(function () {
                store("hk_mission", "");
                if (resumed) log("sys", "Your previous mission has expired. Start a new one.");
            });
    }

    function showMission(st) {
        if (!st) return;
        timerBox.textContent = fmtTime(st.mission_time_s);
        var text = (st.complete ? "DOCKED" : st.status ? st.status.toUpperCase() : "WAITING FOR AGENT") + " · attempts " + st.attempts + " · commands " + st.commands;
        setStatus(text);
        if (st.complete && st.result && !mission.announced) {
            mission.announced = true;
            log("sys", "DOCKED! Mission time " + st.result.time_s.toFixed(1) + " s, #" + st.result.rank + " on the advanced leaderboard.");
        }
    }

    // ------------------------------------------------------------------ boot

    function boot(tries) {
        if (typeof dragon === "undefined" || !renderer) return setTimeout(boot, 200, tries);
        api("/hackathon/config")
            .then(function (c) {
                cfg = c;
                buildPanel();
                if (!cfg.ai_available && MODE !== "advanced") log("sys", "AI is not configured on this server (no API key). Ask the booth staff.");
                ({ beginner: initBeginner, intermediate: initIntermediate, advanced: initAdvanced })[MODE]();
            })
            .catch(function (e) {
                if (e.status === 401) return (location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search));
                if (!e.status && (tries || 0) < 3) return setTimeout(boot, 1000, (tries || 0) + 1); // network blip
                window.dragonBridge = null; // not the hackathon server: behave like the plain sim
            });
    }
    boot(0);
})();

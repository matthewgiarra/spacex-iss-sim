// Programmatic interface to the docking simulator.
//
//   window.dragon.getState()          -> plain JSON-able object describing the vehicle
//   window.dragon.command(name, opts) -> Promise<result>, same actions as the on-screen buttons
//
// It also connects to the optional bridge in server.py so the sim can be driven over HTTP
// from any language. See API.md for the full reference.
(function () {
    "use strict";

    var toDeg = 180 / Math.PI;

    // Docking succeeds when all of these hold at the moment range drops below DOCK_RANGE_M.
    var LIMITS = {
        dock_range_m: 0.2,
        max_attitude_error_deg: toleranceRotation,
        max_speed_per_axis_mps: toleranceRate * PHYSICS_HZ,
        fail_range_m: 500,
    };

    var outcome = null; // "success" | "fail" once an attempt ends
    var outcomeMessage = "";

    // Record why an attempt ended. hideInterface is a global, so wrapping it catches every call.
    var originalHideInterface = window.hideInterface;
    window.hideInterface = function (result) {
        outcome = result;
        outcomeMessage = result === "fail" ? ($("#fail-message").textContent || "").trim() : "Docking successful.";
        return originalHideInterface.apply(this, arguments);
    };

    function round(v, digits) {
        var p = Math.pow(10, digits);
        return Math.round(v * p) / p;
    }

    function status() {
        if (!isEventsEnabled) return "loading";
        if (!isIntroStarted) return "intro";
        if (isGameOver) return outcome || "arriving";
        return "flying";
    }

    function getState() {
        var s = {
            status: status(),
            message: outcome ? outcomeMessage : "",
            paused: simPaused,
            time_s: round(simStepCount / PHYSICS_HZ, 3), // sim seconds since this attempt began
        };
        if (!isWarpComplete) return s;

        // Relative position in the HUD's axes: x = along the docking axis (distance in front
        // of the port), y = left/right, z = up/down. The docking port is the origin.
        var p = { x: camera.position.z - issObject.position.z, y: camera.position.x - issObject.position.x, z: camera.position.y - issObject.position.y };
        // Velocity from thrusters only (what the docking check uses), same axes.
        var v = { x: motionVector.z * PHYSICS_HZ, y: motionVector.x * PHYSICS_HZ, z: motionVector.y * PHYSICS_HZ };
        var range = Math.sqrt(p.x * p.x + p.y * p.y + p.z * p.z);

        s.position = { x: round(p.x, 3), y: round(p.y, 3), z: round(p.z, 3) };
        s.velocity = { x: round(v.x, 4), y: round(v.y, 4), z: round(v.z, 4) };
        s.range = round(range, 3);
        s.range_rate = round(range > 0 ? (p.x * v.x + p.y * v.y + p.z * v.z) / range : 0, 4);
        s.attitude = { roll: round(camera.rotation.z * toDeg, 2), pitch: round(camera.rotation.x * toDeg, 2), yaw: round(camera.rotation.y * toDeg, 2) };
        // Actual body rotation rates (they ease toward the commanded rate over ~1 s).
        s.rotation_rate = {
            roll: round(currentRotationZ * toDeg * PHYSICS_HZ, 3),
            pitch: round(currentRotationX * toDeg * PHYSICS_HZ, 3),
            yaw: round(currentRotationY * toDeg * PHYSICS_HZ, 3),
        };
        // Rates the vehicle is settling toward, from the thruster pulses fired so far.
        s.rotation_rate_commanded = {
            roll: round(-0.01 * targetRotationZ * toDeg * PHYSICS_HZ, 3),
            pitch: round(-0.01 * targetRotationX * toDeg * PHYSICS_HZ, 3),
            yaw: round(-0.01 * targetRotationY * toDeg * PHYSICS_HZ, 3),
        };
        s.precision = {
            translation: translationPulseSize === 0.001 ? "fine" : "coarse",
            rotation: rotationPulseSize === 0.5 ? "fine" : "coarse",
        };
        s.gravity = isGravity;
        return s;
    }

    function requireFlying(name) {
        if (isGameOver) throw new Error("'" + name + "' only works while status is 'flying' (status is '" + status() + "')");
    }

    function pulse(fn) {
        return function (name, opts) {
            requireFlying(name);
            var n = opts && opts.count != null ? Math.floor(opts.count) : 1;
            if (!(n >= 1 && n <= 50)) throw new Error("count must be an integer from 1 to 50");
            for (var i = 0; i < n; i++) fn();
        };
    }

    function setPrecision(kind, level) {
        return function (name) {
            var large = kind === "rotation" ? rotationPulseSize !== 0.5 : translationPulseSize !== 0.001;
            if (large !== (level === "coarse")) updatePrecision(kind);
        };
    }

    var HIDDEN_TAB_HINT = " (is the simulator tab visible? browsers freeze hidden tabs)";

    function waitFor(predicate, timeoutMs, what) {
        return new Promise(function (resolve, reject) {
            var start = Date.now();
            (function check() {
                if (predicate()) return resolve();
                if (Date.now() - start > timeoutMs) return reject(new Error("timed out waiting for " + (what || "the simulator") + HIDDEN_TAB_HINT));
                setTimeout(check, 20);
            })();
        });
    }

    var COMMANDS = {
        // Rotation: each pulse changes the rotation rate about that axis.
        roll_left: pulse(function () { rollLeft(); }),
        roll_right: pulse(function () { rollRight(); }),
        pitch_up: pulse(function () { pitchUp(); }),
        pitch_down: pulse(function () { pitchDown(); }),
        yaw_left: pulse(function () { yawLeft(); }),
        yaw_right: pulse(function () { yawRight(); }),
        // Translation: each pulse changes velocity along the vehicle's own axes.
        translate_forward: pulse(function () { translateForward(); }),
        translate_backward: pulse(function () { translateBackward(); }),
        translate_left: pulse(function () { translateLeft(); }),
        translate_right: pulse(function () { translateRight(); }),
        translate_up: pulse(function () { translateUp(); }),
        translate_down: pulse(function () { translateDown(); }),

        translation_fine: setPrecision("translation", "fine"),
        translation_coarse: setPrecision("translation", "coarse"),
        rotation_fine: setPrecision("rotation", "fine"),
        rotation_coarse: setPrecision("rotation", "coarse"),

        gravity_on: function () { isGravity || toggleGravity(); },
        gravity_off: function () { isGravity && toggleGravity(); },

        // Start a fresh attempt from any state. Resolves once the vehicle is controllable.
        reset: function () {
            var st;
            return waitFor(function () { return isEventsEnabled; }, 120000, "the simulator to load").then(function () {
                st = status();
                if (st === "intro") hideIntro();
                else if (st === "flying") resetPosition();
                else if (st === "success" || st === "fail")
                    // Let the end-of-attempt animation finish first, as a human clicking "play again" would.
                    return waitFor(function () { return !interfaceAnimationOut.isActive(); }, 10000).then(function () {
                        outcome = null;
                        outcomeMessage = "";
                        showInterface();
                    });
                // "arriving": the start sequence is already running.
            }).then(function () {
                return waitFor(function () { return !isGameOver; }, 60000, "the vehicle to arrive at the start point");
            }).then(function () {
                // resetPosition() tweens back to the start point over 5 s; wait it out.
                return st === "flying" ? new Promise(function (r) { setTimeout(r, 5200); }) : null;
            }).then(function () {
                simStepCount = 0; // time_s counts from the start of each attempt
            });
        },

        pause: function () { simPaused = true; },
        resume: function () { simPaused = false; simStepBudget = 0; },

        // While paused, advance the simulation by `seconds` of sim time, as fast as possible.
        step: function (name, opts) {
            var seconds = opts && opts.seconds != null ? Number(opts.seconds) : 1;
            if (!(seconds > 0 && seconds <= 60)) throw new Error("seconds must be > 0 and <= 60");
            if (!simPaused) throw new Error("'step' only works while paused; send 'pause' first");
            simStepBudget += Math.round(seconds * PHYSICS_HZ);
            return waitFor(function () { return simStepBudget === 0; }, 120000, "the step to finish");
        },
    };

    // Run a command. Returns a Promise resolving to { ok, command, state } or { ok: false, error }.
    function command(name, opts) {
        var fn = COMMANDS[name];
        if (!fn) return Promise.resolve({ ok: false, command: name, error: "unknown command '" + name + "'. Valid: " + Object.keys(COMMANDS).join(", "), state: getState() });
        return Promise.resolve()
            .then(function () { return fn(name, opts || {}); })
            .then(
                function () { return { ok: true, command: name, state: getState() }; },
                function (err) { return { ok: false, command: name, error: err.message, state: getState() }; }
            );
    }

    window.dragon = {
        getState: getState,
        command: command,
        commands: Object.keys(COMMANDS),
        limits: LIMITS,
        physicsHz: PHYSICS_HZ,
    };

    // ---- HTTP bridge (only active when served by server.py) ----
    var bridgeUrl = "bridge/sync";
    var pendingResults = [];
    var bridgeConnected = false;
    var commandQueue = Promise.resolve();

    function sync() {
        var results = pendingResults;
        pendingResults = [];
        fetch(bridgeUrl, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ state: getState(), results: results }),
        })
            .then(function (r) {
                if (!r.ok) throw new Error("bridge HTTP " + r.status);
                return r.json();
            })
            .then(function (msg) {
                if (!bridgeConnected) console.log("[dragon] connected to API bridge");
                bridgeConnected = true;
                // Run commands strictly in order; each finishes before the next starts.
                (msg.commands || []).forEach(function (c) {
                    commandQueue = commandQueue.then(function () {
                        return command(c.command, c).then(function (res) {
                            res.id = c.id;
                            pendingResults.push(res);
                        });
                    });
                });
                setTimeout(sync, 30);
            })
            .catch(function () {
                // Not served by server.py (e.g. plain `python -m http.server`), or it went away.
                if (bridgeConnected) console.log("[dragon] lost API bridge; retrying");
                bridgeConnected = false;
                pendingResults = results.concat(pendingResults);
                setTimeout(sync, 2000);
            });
    }
    sync();
})();

/*
 * autopilot.c: a Dragon autopilot in C. Edit controller_update() below.
 *
 * Build (from the kit folder):
 *     cc -I include examples/autopilot.c -o autopilot -lcurl -lm              (macOS, Linux)
 *     gcc -I include examples/autopilot.c -o autopilot.exe -lwinhttp          (Windows, MinGW)
 *     cl /I include examples\autopilot.c                                      (Windows, Visual Studio)
 * Run with your autopilot URL from the Autopilot page:
 *     ./autopilot https://<site>/ap/<your-key>             then press LAUNCH in the simulator tab
 *     ./autopilot https://<site>/ap/<your-key> --launch    or let this program launch the flight
 *
 * State, control inputs and rules: see autopilot.py or AUTOPILOT_API.md (the same for every language).
 */
#include "hackathon_client.h" /* first: it sets up the system headers */

#include <math.h>
#include <stdio.h>
#include <string.h>

#define STALE_MS 2000      /* skip telemetry that arrives this long after the previous one */
#define ACT_EVERY_MS 500   /* only act this often, so we see the effect of each burn before the next */
#define ROTATION_PULSE 0.3 /* deg/s per rotation pulse */

static long long since_action_ms = ACT_EVERY_MS;

/* This example only levels out the attitude, so the Dragon ends up pointing at the station...
 * and then sits there. Making it actually dock (fast, and inside the approach corridor) is your job!
 * Fill inputs[6] = x, y, z, roll, pitch, yaw thruster pulses. */
static void controller_update(const double state[12], const double commanded_rates[3], long long dt_ms, double inputs[6]) {
    int i;
    memset(inputs, 0, 6 * sizeof inputs[0]);
    since_action_ms += dt_ms;
    if (since_action_ms < ACT_EVERY_MS) return;
    since_action_ms = 0;
    for (i = 0; i < 3; i++) {
        /* Aim for a rotation rate that brings the angle back toward 0, slowing down as it gets close. */
        double target_rate = fmax(-3.0, fmin(3.0, -0.25 * state[HK_ROLL + i]));
        inputs[HK_IN_ROLL + i] = round((target_rate - commanded_rates[i]) / ROTATION_PULSE);
    }
}

int main(int argc, char **argv) {
    hk_connection *connection;
    hk_telemetry data;
    hk_reply reply;
    char flight_id[40] = "";
    long long timestamp_ms = 0, last_print_ms = -1000000;
    double inputs[6];
    int i;

    setvbuf(stdout, NULL, _IONBF, 0);  /* show output immediately, even when it goes to a file */
    if (argc < 2 || !(connection = hk_connect(argv[1], 10000))) {
        fprintf(stderr, "Usage: autopilot <your autopilot URL> [--launch]   (copy the URL from the Autopilot page)\n");
        return 1;
    }
    if (argc > 2 && strcmp(argv[2], "--launch") == 0) {
        if (hk_launch(connection, &reply) != 0) { fprintf(stderr, "%s\n", hk_last_error(connection)); return 1; }
        printf(reply.ok ? "Launching...\n" : "Couldn't launch: %s\n", reply.error);
    }
    printf("Waiting for a flight. Press LAUNCH in the simulator tab (or run with --launch).\n");

    for (;;) {
        if (hk_request_telemetry(connection, &data) != 0) { fprintf(stderr, "%s\n", hk_last_error(connection)); return 1; }
        if (flight_id[0] == '\0') {
            if (strcmp(data.status, "offline") == 0) { printf("%s\n", data.message); hk_sleep_ms(2000); continue; }  /* no simulator tab */
            if (strcmp(data.status, "flying") != 0) { hk_sleep_ms(50); continue; }
            memcpy(flight_id, data.flight_id, sizeof flight_id);
            timestamp_ms = data.timestamp_ms;
            printf("Flight %s started. Flying!\n", flight_id);
            continue;
        }

        /* The flight is over: docked, failed, or replaced by a new one. */
        if (strcmp(data.status, "flying") != 0 || strcmp(data.flight_id, flight_id) != 0) {
            if (data.docked) printf("*** DOCKED in %.1f s (#%d on the leaderboard) ***\n", data.timestamp_ms / 1000.0, data.rank);
            else printf("--- Flight over: %s ---\n", data.message);
            break;
        }

        {
            long long dt = data.timestamp_ms - timestamp_ms;
            if (dt == 0) { hk_sleep_ms(10); continue; }  /* nothing new yet; don't spam the website */
            timestamp_ms = data.timestamp_ms;
            if (dt > STALE_MS) continue;

            if (timestamp_ms - last_print_ms >= 5000) {
                last_print_ms = timestamp_ms;
                printf("t=%6.1fs  pos=(%6.1f,%5.1f,%5.1f) m  att=(%5.1f,%5.1f,%5.1f) deg\n", timestamp_ms / 1000.0,
                       data.state[HK_X], data.state[HK_Y], data.state[HK_Z], data.state[HK_ROLL], data.state[HK_PITCH], data.state[HK_YAW]);
            }
            controller_update(data.state, data.commanded_rates, dt, inputs);
        }
        for (i = 0; i < 6 && inputs[i] == 0; i++) {}
        if (i < 6) {
            if (hk_send_control_inputs(connection, inputs, &reply) != 0) { fprintf(stderr, "%s\n", hk_last_error(connection)); return 1; }
            if (!reply.ok) printf("Control inputs not applied: %s\n", reply.error);
        }
    }
    hk_close(connection);
    return 0;
}

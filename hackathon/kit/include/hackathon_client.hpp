// hackathon_client.hpp: header-only C++ client for the Dragon Docking Challenge (C++11 or newer).
//
// Request-reply with your simulator through the challenge website, over HTTPS.
// A thin wrapper over hackathon_client.h (keep both files together).
//
//     #include "hackathon_client.hpp"      (include it first, before other headers)
//
//     hackathon::Client client("https://<site>/ap/<your-key>");   // your autopilot URL
//     hackathon::Telemetry data = client.requestTelemetry();
//     client.sendControlInputs({0, 0, -2, 0, 1, 0});              // x, y, z, roll, pitch, yaw pulses
//
// Build: macOS/Linux  c++ -std=c++11 -I include examples/autopilot.cpp -o autopilot -lcurl
//        Windows      g++ -std=c++11 -I include examples/autopilot.cpp -o autopilot.exe -lwinhttp   (MinGW)
//                     cl /EHsc /I include examples\autopilot.cpp                                    (Visual Studio)
//
// Requests throw hackathon::Error when they don't get through (no network, wrong or expired URL, timeout).
// Replies come back with ok == false and error set when the request was refused (for example "no flight
// in progress"). See AUTOPILOT_API.md.
#ifndef HACKATHON_CLIENT_HPP
#define HACKATHON_CLIENT_HPP

#include <array>
#include <stdexcept>
#include <string>

#include "hackathon_client.h"

namespace hackathon {

// Indexes into Telemetry::state. Units: metres, m/s, degrees, deg/s; position is relative to the docking port.
enum StateIndex { X, Y, Z, X_DOT, Y_DOT, Z_DOT, ROLL, PITCH, YAW, ROLL_DOT, PITCH_DOT, YAW_DOT };

struct Error : std::runtime_error {
    explicit Error(const std::string &what) : std::runtime_error(what) {}
};

struct Telemetry {
    bool ok = false;
    std::string status;                  // "flying", "waiting", "launching", "docked", "failed" or "offline"
    bool docked = false;
    std::string flightId;                // changes with every flight; empty when there's none
    long long timestampMs = 0;           // sim time in this flight; unchanged = nothing new yet
    bool hasState = false;               // false when there's no flight (state is then all zeros)
    std::array<double, 12> state{};      // x, y, z, x_dot, y_dot, z_dot, roll, pitch, yaw, roll_dot, pitch_dot, yaw_dot
    std::array<double, 3> commandedRates{};  // roll, pitch, yaw rates (deg/s) the vehicle is easing toward
    int rank = 0;                        // leaderboard place after docking; 0 if none
    std::string message;                 // why a flight ended, or what's going on
    std::string error;
};

struct Reply {
    bool ok = false;
    long long timestampMs = 0;           // control: sim time your inputs took effect
    std::array<int, 6> pulses{};         // control: the whole pulses actually fired on each axis
    std::string flightId;                // launch: the new flight
    std::string error;
};

class Client {
public:
    explicit Client(const std::string &url, int timeoutMs = 10000) : c_(hk_connect(url.c_str(), timeoutMs)) {
        if (!c_) throw Error("expected your autopilot URL from the Autopilot page, like https://<site>/ap/<your-key>; got '" + url + "'");
    }
    ~Client() { hk_close(c_); }
    Client(Client &&other) noexcept : c_(other.c_) { other.c_ = nullptr; }
    Client &operator=(Client &&other) noexcept {
        if (this != &other) { hk_close(c_); c_ = other.c_; other.c_ = nullptr; }
        return *this;
    }
    Client(const Client &) = delete;
    Client &operator=(const Client &) = delete;

    // The latest telemetry. It never predates your last control inputs taking effect.
    Telemetry requestTelemetry() {
        hk_telemetry t;
        check(hk_request_telemetry(c_, &t));
        Telemetry out;
        out.ok = t.ok != 0;
        out.status = t.status;
        out.docked = t.docked != 0;
        out.flightId = t.flight_id;
        out.timestampMs = t.timestamp_ms;
        out.hasState = t.has_state != 0;
        for (int i = 0; i < 12; i++) out.state[i] = t.state[i];
        for (int i = 0; i < 3; i++) out.commandedRates[i] = t.commanded_rates[i];
        out.rank = t.rank;
        out.message = t.message;
        out.error = t.error;
        return out;
    }

    // Fire thrusters: x, y, z, roll, pitch, yaw pulses (rounded to whole pulses; limited to +/-50).
    Reply sendControlInputs(const std::array<double, 6> &inputs) {
        hk_reply r;
        check(hk_send_control_inputs(c_, inputs.data(), &r));
        return convert(r);
    }

    // Start a flight, as the LAUNCH button does. The Dragon takes a few seconds to arrive.
    Reply launch() {
        hk_reply r;
        check(hk_launch(c_, &r));
        return convert(r);
    }

    // End the current flight (it counts as a failed attempt).
    Reply abort(const std::string &reason = "") {
        hk_reply r;
        check(hk_abort(c_, reason.c_str(), &r));
        return convert(r);
    }

private:
    hk_connection *c_;

    void check(int rc) const {
        if (rc != 0) throw Error(hk_last_error(c_));
    }
    static Reply convert(const hk_reply &r) {
        Reply out;
        out.ok = r.ok != 0;
        out.timestampMs = r.timestamp_ms;
        for (int i = 0; i < 6; i++) out.pulses[i] = r.pulses[i];
        out.flightId = r.flight_id;
        out.error = r.error;
        return out;
    }
};

inline Client connect(const std::string &url, int timeoutMs = 10000) { return Client(url, timeoutMs); }

inline void sleepMs(int ms) { hk_sleep_ms(ms); }

}  // namespace hackathon

#endif  // HACKATHON_CLIENT_HPP

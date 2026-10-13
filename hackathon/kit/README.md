# Dragon Autopilot starter kit

Write an autopilot that docks SpaceX's Dragon with the International Space Station, run it from your laptop, and post your time to the leaderboard.

The simulator runs in real time in your browser. Your autopilot sends requests to the challenge website: "give me the latest telemetry", "fire these thrusters". **Any language works**: Python, C and C++ clients are included, and anything that can send an HTTPS request can speak the JSON protocol in `AUTOPILOT_API.md`.

| File | What it is |
|---|---|
| `autopilot.py` | **Your autopilot, in Python.** Edit the `Controller` class. |
| `hackathon/client.py` | **`hackathon.client`**: the Python client (standard library only). |
| `include/hackathon_client.h`, `include/hackathon_client.hpp` | Header-only C and C++ clients. |
| `examples/autopilot.c`, `examples/autopilot.cpp` | The same autopilot in C and C++. |
| `AUTOPILOT_API.md` | Full reference: state, inputs, rules, protocol, troubleshooting. |

You need **Python 3.8 or newer** for the Python example; nothing else to install. Check with `python --version` (on some Macs, `python3`). On Windows without admin rights, install Python from [python.org](https://www.python.org/downloads/windows/) and **untick "Install launcher for all users"**, or use the Microsoft Store version.

## Fly in 5 minutes

1. On the challenge website, open **Autopilot**, type your callsign, and press **GET AUTOPILOT LINK**. Keep that browser tab open and visible: your Dragon flies there.
2. Copy your autopilot URL from the panel.
3. In a terminal, in this folder:
   ```bash
   python autopilot.py https://<site>/ap/<your-key>
   ```
4. Press **LAUNCH** in the browser. Your autopilot gets the latest telemetry as fast as it asks and fires the thrusters its controller decides on.

The example in `autopilot.py` levels the Dragon out and then just sits there, so it won't dock. That's your job: make it dock as fast as possible without breaking the rules.

**The loop:** edit your autopilot, run it again, press LAUNCH. Or add `--launch` to the command and it launches the flight itself.

**C or C++?** Build the example from this folder, then run it with your URL the same way. macOS has everything built in; Linux needs libcurl's development package; on Windows the clients use WinHTTP, which is built in.

```bash
cc -I include examples/autopilot.c -o autopilot -lcurl -lm                          # C, macOS/Linux
c++ -std=c++11 -I include examples/autopilot.cpp -o autopilot -lcurl                # C++, macOS/Linux
gcc -I include examples/autopilot.c -o autopilot.exe -lwinhttp                      # C, Windows (MinGW)
g++ -std=c++11 -I include examples/autopilot.cpp -o autopilot.exe -lwinhttp         # C++, Windows (MinGW)
```

## The rules in one breath

Start about 200 m out, offset, and tilted. Dock with range under 0.2 m, attitude within 0.2° on every axis, and each velocity under 0.24 m/s. Keep your **closing speed under 0.1 + 0.02 × range** m/s the whole way (the approach corridor). Don't touch the station anywhere else, and dock within 600 s. Fastest simulated time wins. Details in `AUTOPILOT_API.md`.

## Use an AI

You're encouraged to have an AI help you write your autopilot. Paste something like this into your favourite assistant, along with `AUTOPILOT_API.md` and the example for your language:

> I'm writing an autopilot for a spacecraft docking simulator that runs in real time. The attached `AUTOPILOT_API.md` explains the telemetry I get, the thruster inputs I can send, and the rules; the attached example shows the control loop. Write a controller that docks as fast as possible while never breaking the approach-corridor rule. Explain how it works so I can tune it.

Then fly it, read what goes wrong in the terminal output, and iterate. The fastest docking times come from understanding why a flight failed or was slow, then fixing that.

## Troubleshooting

| Message | Fix |
|---|---|
| `Usage: python autopilot.py <your autopilot URL>` | Add your URL from the Autopilot page to the command. |
| `No module named hackathon` | Run from this folder (the one containing the `hackathon` folder). |
| `unknown or expired autopilot URL` | Links last a few hours. Press GET AUTOPILOT LINK again and use the new URL. |
| `couldn't reach ...` | Check your internet connection and the URL. |
| `couldn't verify the website's certificate` (Mac) | Run `Install Certificates.command` in your Python folder (Applications → Python 3.x). |
| `The simulator tab ... isn't connected` / stuck at `Waiting for a flight` | Keep the simulator tab open and visible: browsers freeze hidden tabs. |
| `Control inputs not applied: no flight in progress` | The flight had already ended. The next telemetry says how. |

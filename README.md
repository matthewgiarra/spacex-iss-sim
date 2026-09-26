# Spacex ISS Simulator
Run the [SpaceX ISS Docking Simulator](https://iss-sim.spacex.com/) locally, and fly it from code.

# Quick Start
1. Clone this repository
    ```bash
    git clone https://github.com/matthewgiarra/spacex-iss-sim
    ```

2. Start the local server (Python 3, no packages needed)
    ```bash
    cd spacex-iss-sim
    python server.py
    ```

3. In a browser, navigate to [http://localhost:5555](http://localhost:5555)

That should be it. The simulator should load in your browser window. Have at it.

# Programmatic control
While the page is open, `server.py` exposes the simulator as a JSON API:

```bash
curl -X POST localhost:5555/api/command -d '{"command": "reset"}'
curl localhost:5555/api/state
curl -X POST localhost:5555/api/command -d '{"command": "pitch_up", "count": 2}'
```

```python
from dragon import Dragon
sim = Dragon()
sim.reset()
print(sim.state())
```

- [API.md](API.md): full reference covering state, commands, coordinates, docking criteria, and pause/step mode.
- [dragon.py](dragon.py): Python client.
- [examples/autopilot.py](examples/autopilot.py): a simple non-AI controller that docks successfully. It's the baseline to beat.

Changes from the original simulator: physics runs at a fixed 60 Hz instead of once per rendered frame, so it flies the same on every display. A small API layer ([js/api.js](iss-sim.spacex.com/js/api.js)) adds pause/step support.

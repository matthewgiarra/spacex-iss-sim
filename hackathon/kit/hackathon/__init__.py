"""Dragon Docking Challenge: connect your autopilot to the simulator.

    import hackathon.client as hackathon_client
    connection = hackathon_client.connect("https://<site>/ap/<your-key>")

Request-reply over HTTPS: request the latest telemetry, send thruster inputs. Standard library only.
C and C++ users can use the header-only clients in include/. See AUTOPILOT_API.md.
"""

__version__ = "3.0"

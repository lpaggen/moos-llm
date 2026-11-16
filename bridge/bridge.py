import socket
import json
import configparser
from pathlib import Path


_CONFIG = configparser.ConfigParser(interpolation=None)
_CONFIG.read(Path(__file__).resolve().parent.parent / "project.ini")


def get(section, key):
    return _CONFIG[section][key]


def get_int(section, key):
    return _CONFIG.getint(section, key)


def get_float(section, key):
    return _CONFIG.getfloat(section, key)


DEFAULT_HOST = get("hosts", "bridge")
DEFAULT_PORT = get_int("ports", "bridge")
DEFAULT_TIMEOUT_S = get_float("runtime", "bridge_timeout_seconds")


def _recv_json_line(sock, buffer=b""):
    while b"\n" not in buffer:
        chunk = sock.recv(4096)
        if not chunk:
            raise RuntimeError("C++ bridge disconnected before sending JSON")
        buffer += chunk

    line, buffer = buffer.split(b"\n", 1)
    return json.loads(line.decode("utf-8")), buffer


def get_bridge_json(host=DEFAULT_HOST, port=DEFAULT_PORT):
    """Request and return a raw, complete own-ship bridge snapshot."""
    request = {
        "type": "query",
        "target": "own_ship",
    }

    with socket.create_connection((host, port), timeout=DEFAULT_TIMEOUT_S) as sock:
        sock.settimeout(DEFAULT_TIMEOUT_S)
        sock.sendall((json.dumps(request) + "\n").encode("utf-8"))
        buffer = b""
        while True:
            msg, buffer = _recv_json_line(sock, buffer)
            # The C++ bridge also publishes unsolicited ``state`` messages.
            # Do not mistake one of those for the richer query response.
            if msg.get("type") == "own_ship":
                return msg


def read_own_ship(stream):
    """Request the latest own-ship snapshot over an already-open bridge stream."""
    request = {
        "type": "query",
        "target": "own_ship",
    }

    stream.write(json.dumps(request) + "\n")
    stream.flush()

    line = stream.readline()
    if not line:
        raise RuntimeError("C++ bridge disconnected before sending JSON")
    return json.loads(line)


def get_own_ship(host=DEFAULT_HOST, port=DEFAULT_PORT):
    """Request the bridge's latest own-ship snapshot and contact cache."""
    return get_bridge_json(host, port)


def send_command(command, host=DEFAULT_HOST, port=DEFAULT_PORT):
    """Send one command or a multi-command envelope to the bridge."""
    with socket.create_connection((host, port), timeout=DEFAULT_TIMEOUT_S) as sock:
        sock.settimeout(DEFAULT_TIMEOUT_S)
        sock.sendall((json.dumps(command) + "\n").encode("utf-8"))


def set_simulation_paused(paused, host=DEFAULT_HOST, port=DEFAULT_PORT):
    """Set fleet pause state and wait until the bridge has posted it.

    The bridge emits unsolicited state records, so consume records until the
    command-specific acknowledgement arrives.
    """
    command = {
        "type": "command",
        "class": "simulation",
        "action": "pause" if paused else "resume",
    }
    with socket.create_connection((host, port), timeout=DEFAULT_TIMEOUT_S) as sock:
        sock.settimeout(DEFAULT_TIMEOUT_S)
        sock.sendall((json.dumps(command) + "\n").encode("utf-8"))
        buffer = b""
        while True:
            msg, buffer = _recv_json_line(sock, buffer)
            if msg.get("type") == "simulation_state":
                if bool(msg.get("paused")) != bool(paused):
                    raise RuntimeError("bridge acknowledged the wrong simulation state")
                return


def simplify_state(state):
    return {
        "own_ship": {
            "x": state["nav"]["x"],
            "y": state["nav"]["y"],
            "heading": state["nav"]["heading"],
            "speed": state["nav"]["speed"],
        },
        "contacts": [
            {
                "name": c["name"],
                "range": c["range"],
                "age_s": c.get("age_s"),
                "x": c["nav"]["x"],
                "y": c["nav"]["y"],
                "heading": c["nav"]["heading"],
                "speed": c["nav"]["speed"],
            }
            for c in state.get("contacts", [])
        ],
        "current_action": state.get("current_action"),
        "mission_objective": state.get("mission_objective"),
        "next_waypoint": state.get("next_waypoint"),
        "temporary_waypoint": state.get("temporary_waypoint"),
        "action_underway": state.get("action_underway", False),
        "paused": state.get("paused", False),
    }

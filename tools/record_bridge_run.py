#!/usr/bin/env python3
"""Record bridge telemetry for a controller that does not use the LLM agents."""

from __future__ import annotations

import argparse
import json
import socket
import time
from datetime import datetime, timezone
from pathlib import Path


def emit(handle, event: str, **fields) -> None:
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **fields,
    }
    handle.write(json.dumps(row, separators=(",", ":")) + "\n")
    handle.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--controller", default="moos-ivp")
    parser.add_argument("--idle-timeout", type=float, default=30.0)
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as output:
        emit(output, "startup", controller=args.controller)
        with socket.create_connection((args.host, args.port), timeout=10.0) as connection:
            stream = connection.makefile("r", encoding="utf-8")
            last_data = time.monotonic()
            cycle = 0
            while True:
                connection.settimeout(max(0.1, args.idle_timeout - (time.monotonic() - last_data)))
                line = stream.readline()
                if not line:
                    raise RuntimeError("bridge connection closed before mission completion")
                last_data = time.monotonic()
                state = json.loads(line)
                if state.get("type") not in {"state", "own_ship"}:
                    continue
                cycle += 1
                emit(output, "bridge_snapshot", cycle_id=cycle, state=state)
                if state.get("mission_complete"):
                    emit(output, "mission_complete", cycle_id=cycle)
                    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Wait for a healthy local model API with the requested model registered."""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request

from tools.config_value import get


def probe(base_url: str, api_key: str, model: str) -> None:
    headers = {"Authorization": f"Bearer {api_key}"}
    for path in ("/health", "/v1/models"):
        request = urllib.request.Request(base_url + path, headers=headers)
        with urllib.request.urlopen(request, timeout=2) as response:
            if path == "/v1/models":
                models = json.load(response)
                if model not in {item["id"] for item in models["data"]}:
                    raise ValueError(f"model {model!r} is not registered")


def wait_ready(base_url: str, api_key: str, model: str, timeout: float,
               process_pid: int | None = None) -> None:
    deadline = time.monotonic() + timeout
    last_error = "API unavailable"
    while time.monotonic() < deadline:
        if process_pid is not None:
            try:
                os.kill(process_pid, 0)
                with open(f"/proc/{process_pid}/stat") as status:
                    if status.read().split(") ", 1)[1].split()[0] == "Z":
                        raise ProcessLookupError
            except (ProcessLookupError, FileNotFoundError):
                raise RuntimeError("vLLM supervisor exited; inspect logs/vllm.log") from None
        try:
            probe(base_url, api_key, model)
            return
        except (OSError, ValueError, KeyError) as error:
            last_error = str(error)
        time.sleep(min(0.25, max(0, deadline - time.monotonic())))
    raise RuntimeError(f"vLLM readiness timed out after {timeout:g}s: {last_error}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--process-pid", type=int)
    args = parser.parse_args()
    base_url = f"http://{get('hosts', 'vllm')}:{get('ports', 'vllm')}"
    try:
        wait_ready(base_url, get("llm", "api_key"), args.model,
                   args.timeout, args.process_pid)
    except RuntimeError as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()

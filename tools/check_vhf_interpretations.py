#!/usr/bin/env python3
"""Run every deterministic VHF transcript through the live interpreter."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any

from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.agent_vhf import AgentVHF
from mission.render_fleet import load_fleet
from tools.config_value import get
from tools.inject_vhf_scenario import load_manifest


def same(expected: Any, actual: Any) -> bool:
    return str(expected or "").strip().casefold() == str(actual or "").strip().casefold()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenarios",
        type=Path,
        default=ROOT / "mission" / "scenarios" / "vhf",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    host = get("hosts", "vllm")
    port = get("ports", "vllm")
    model = get("models", "default_local")
    client = OpenAI(
        base_url=f"http://{host}:{port}/v1",
        api_key=get("llm", "api_key"),
    )
    agent = AgentVHF.__new__(AgentVHF)
    agent.client = client
    agent.model = model
    agent.own_ship = "ALPHA"

    results: list[dict[str, Any]] = []
    for manifest_path in sorted(args.scenarios.glob("*/*.vhf.json")):
        manifest = load_manifest(manifest_path)
        fleet_path = manifest_path.with_suffix("").with_suffix(".ini")
        fleet = load_fleet(fleet_path, None, False)
        known_vessels = list(fleet["names"])
        for event in manifest["events"]:
            expected = event.get("expected_interpretation") or {}
            paused = expected.get("recommended_action") == "resume"
            snapshot = SimpleNamespace(
                version=1,
                state_for_agent=lambda names=known_vessels, held=paused: {
                    "vehicle": "ALPHA",
                    "contacts": [
                        {"name": name} for name in names if name != "ALPHA"
                    ],
                    "paused": held,
                },
            )
            result: dict[str, Any] = {
                "scenario_id": manifest["scenario_id"],
                "event_id": event["event_id"],
            }
            try:
                interpretation = agent.interpret(
                    event["transcript"],
                    bridge_snapshot=snapshot,
                    channel=event["channel"],
                    known_vessels=known_vessels,
                    language="en",
                ).model_dump(mode="json")
                checks = {
                    key: same(value, interpretation.get(key))
                    for key, value in expected.items()
                }
                result.update({
                    "passed": bool(checks) and all(checks.values()),
                    "checks": checks,
                    "interpretation": interpretation,
                })
            except Exception as exc:  # contract probe must report every event
                result.update({"passed": False, "error": str(exc)})
            results.append(result)
            print(
                f"{result['scenario_id']} {result['event_id']}: "
                f"{'PASS' if result['passed'] else 'FAIL'}",
                flush=True,
            )

    report = {
        "model": model,
        "events": results,
        "passed": sum(bool(item["passed"]) for item in results),
        "total": len(results),
        "all_passed": bool(results) and all(bool(item["passed"]) for item in results),
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(f"Passed {report['passed']}/{report['total']} transcript contracts")
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

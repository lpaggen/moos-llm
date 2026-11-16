#!/usr/bin/env python3
"""Prepare, submit, and validate captain-critic teacher batches.

The source is the final accepted OOW dataset.  Each retained state produces an
approval request containing the accepted OOW control and, optionally, one
counterfactual request containing a deterministically corrupted proposal.  The
gold control is stored only in the local manifest and is never sent to the
teacher.  Conversion keeps only:

* gold proposals that the teacher approves unchanged; and
* counterfactual proposals that the teacher rejects and replaces with the
  accepted gold control.

This makes approval/rejection labels useful for critic SFT while preventing the
teacher from seeing the answer through privileged manifest metadata.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import random
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.agent_captain import AgentCaptain, RETRIEVAL_CLEARANCE_M
from agents.schemas.captain import CaptainRagDecision
from bridge.state import BridgeSnapshot


DEFAULT_DATASET = Path("sft/output/privileged_teacher_narrative_directional_v7")
DEFAULT_WORK_DIR = Path("sft/output/captain_critic_teacher_v1")
SPLIT_FILES = {
    "train": "train_natural.jsonl",
    "validation": "validation_natural.jsonl",
    "test": "test_natural.jsonl",
}
MODEL_PRICES = {
    "gpt-6-luna": (0.05, 0.25),
    "gpt-6.1-terra": (0.50, 2.50),
    "gpt-6-sol": (1.00, 5.00),
    "gpt-6.1-sol": (1.00, 5.00),
    "gpt-6-astra": (5.00, 25.00),
}
SUPPORTED_EFFORTS = {"low", "medium", "high", "xhigh", "max"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if line.strip():
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_request_shards(
    directory: Path,
    rows: list[dict[str, Any]],
    maximum_bytes: int,
    maximum_enqueued_tokens: int,
) -> list[dict[str, Any]]:
    """Write Batch files below both upload and organization queue limits."""
    shards: list[list[str]] = [[]]
    sizes = [0]
    token_counts = [0]
    for row in rows:
        encoded = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
        size = len(encoded.encode("utf-8"))
        body = row.get("body") or {}
        estimated_tokens = approximate_tokens(
            str(body.get("instructions") or "")
            + str(body.get("input") or "")
            + json.dumps(body.get("text") or {}, ensure_ascii=False)
        ) + int(body.get("max_output_tokens") or 0)
        if size > maximum_bytes:
            raise ValueError(f"one request is larger than the configured shard limit: {size}")
        if estimated_tokens > maximum_enqueued_tokens:
            raise ValueError(
                "one request is larger than the configured enqueued-token limit: "
                f"{estimated_tokens}"
            )
        if shards[-1] and (
            sizes[-1] + size > maximum_bytes
            or token_counts[-1] + estimated_tokens > maximum_enqueued_tokens
        ):
            shards.append([])
            sizes.append(0)
            token_counts.append(0)
        shards[-1].append(encoded)
        sizes[-1] += size
        token_counts[-1] += estimated_tokens
    result = []
    for index, (lines, size, tokens) in enumerate(
        zip(shards, sizes, token_counts), start=1
    ):
        path = directory / f"batch_input_{index:03d}.jsonl"
        path.write_text("".join(lines), encoding="utf-8")
        result.append({
            "path": str(path),
            "requests": len(lines),
            "bytes": size,
            "estimated_enqueued_tokens": tokens,
        })
    return result


def completion(row: dict[str, Any]) -> dict[str, Any]:
    value = json.loads(row["completion"][-1]["content"])
    if "turn_direction" in value:
        magnitude = float(value.get("turn_degrees") or 0.0)
        direction = value.get("turn_direction")
        degrees = -magnitude if direction == "port" else magnitude if direction == "starboard" else 0.0
    else:
        degrees = float(value.get("degrees") or 0.0)
    return {
        "action": value["action"],
        "degrees": degrees,
        "speed": float(value.get("speed") or 0.0),
        "reason": str(value.get("reason") or "Accepted teacher control."),
        "auxiliary_commands": value.get("auxiliary_commands") or [],
    }


def same_control(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        left.get("action") == right.get("action")
        and abs(float(left.get("degrees") or 0.0) - float(right.get("degrees") or 0.0)) < 1e-6
        and abs(float(left.get("speed") or 0.0) - float(right.get("speed") or 0.0)) < 1e-6
    )


def source_cycles(path: Path) -> list[dict[str, Any]]:
    by_cycle: dict[int, dict[str, Any]] = defaultdict(dict)
    for event in read_jsonl(path):
        cycle = event.get("cycle_id")
        if not isinstance(cycle, int):
            continue
        name = event.get("event")
        if name in {"bridge_snapshot", "oow_prompt_input", "oow_decision"}:
            by_cycle[cycle][str(name)] = event
    result = []
    for cycle, events in sorted(by_cycle.items()):
        if {"bridge_snapshot", "oow_prompt_input", "oow_decision"}.issubset(events):
            result.append({"runtime_cycle_id": cycle, **events})
    return result


def match_runtime_cycle(row: dict[str, Any], cycles: list[dict[str, Any]]) -> dict[str, Any]:
    narrative = row["prompt"][-1]["content"]
    gold = completion(row)
    matches = [
        item for item in cycles
        if item["oow_prompt_input"].get("narrative") == narrative
        and same_control(item["oow_decision"].get("decision") or {}, gold)
    ]
    if len(matches) == 1:
        return matches[0]
    # Narrative-ablation datasets intentionally rewrite the recorded runtime
    # user message.  Their stable example ID preserves the one-based ordinal
    # of the model decision in the source audit.
    marker = "::decision::"
    example_id = str(row.get("example_id") or "")
    if marker in example_id:
        ordinal = int(example_id.rsplit(marker, 1)[1])
        if 1 <= ordinal <= len(cycles):
            candidate = cycles[ordinal - 1]
            if same_control(candidate["oow_decision"].get("decision") or {}, gold):
                return candidate
    raise ValueError(
        f"{row.get('example_id')}: no unique narrative match and ordinal fallback failed"
    )


def counterfactual(gold: dict[str, Any], bridge_state: dict[str, Any]) -> dict[str, Any]:
    action = gold["action"]
    proposal = {
        "action": "maintain_course",
        "degrees": 0.0,
        "speed": 0.0,
        "reason": "OOW proposal supplied for independent captain review.",
        "auxiliary_commands": [],
    }
    if action == "alter_course":
        proposal.update(action="alter_course", degrees=-float(gold["degrees"]))
    elif action == "set_speed":
        proposal.update(action="maintain_course")
    elif action == "rejoin_route":
        proposal.update(action="maintain_course")
    elif action == "resume":
        proposal.update(action="stop")
    elif action == "stop":
        proposal.update(action="resume")
    elif action == "skip":
        proposal.update(action="resume")
    else:
        contacts = bridge_state.get("contacts") or []
        contact = min(
            (item for item in contacts if isinstance(item, dict)),
            key=lambda item: float(item.get("range") or float("inf")),
            default=None,
        )
        if contact is None:
            proposal.update(action="stop")
        else:
            bearing = float(contact.get("relative_bearing") or 0.0) % 360.0
            toward = 30.0 if 0.0 <= bearing <= 180.0 else -30.0
            proposal.update(action="alter_course", degrees=toward)
    if same_control(proposal, gold):
        proposal.update(action="stop", degrees=0.0, speed=0.0)
    return proposal


def request_id(example_id: str, variant: str) -> str:
    digest = hashlib.sha256(f"{example_id}\n{variant}".encode()).hexdigest()[:20]
    return f"captain-{variant}-{digest}"


def request_record(
    identifier: str,
    model: str,
    effort: str,
    system_prompt: str,
    payload: dict[str, Any],
    max_output_tokens: int,
) -> dict[str, Any]:
    return {
        "custom_id": identifier,
        "method": "POST",
        "url": "/v1/responses",
        "body": {
            "model": model,
            "instructions": system_prompt,
            "input": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            "reasoning": {"effort": effort},
            "max_output_tokens": max_output_tokens,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "captain_critic_decision",
                    "strict": True,
                    "schema": CaptainRagDecision.model_json_schema(mode="validation"),
                }
            },
        },
    }


def approximate_tokens(value: Any) -> int:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return math.ceil(len(text.encode("utf-8")) / 3.7)


def prepare(args: argparse.Namespace) -> None:
    if args.reasoning_effort not in SUPPORTED_EFFORTS:
        raise ValueError(f"unsupported reasoning effort: {args.reasoning_effort}")
    if args.work_dir.exists() and any(args.work_dir.iterdir()):
        raise FileExistsError(f"refusing to overwrite non-empty directory: {args.work_dir}")
    captain = AgentCaptain(client=None, validator_queue=asyncio.Queue(), maib_retriever=None)
    source_cache: dict[Path, list[dict[str, Any]]] = {}
    requests: list[dict[str, Any]] = []
    manifests: list[dict[str, Any]] = []
    input_tokens = 0
    counts: Counter[str] = Counter()
    variants = [item.strip() for item in args.variants.split(",") if item.strip()]
    if not variants or not set(variants).issubset({"gold", "counterfactual"}):
        raise ValueError("--variants must contain gold and/or counterfactual")

    for split, filename in SPLIT_FILES.items():
        rows = read_jsonl(args.dataset_dir / filename)
        if args.limit_per_split is not None:
            rows = rows[: args.limit_per_split]
        for row in rows:
            source = Path(row["source_file"])
            if source not in source_cache:
                source_cache[source] = source_cycles(source)
            cycles = source_cache[source]
            matched = match_runtime_cycle(row, cycles)
            raw = matched["bridge_snapshot"]["state"]
            snapshot = BridgeSnapshot(
                version=int(matched["runtime_cycle_id"]),
                received_at=float(matched["bridge_snapshot"].get("received_at") or 0.0),
                _raw_state=raw,
            )
            gold = completion(row)
            proposals = {"gold": gold}
            observed = captain.observations_only_state(snapshot.state_for_agent())
            proposals["counterfactual"] = counterfactual(gold, observed)
            for variant in variants:
                proposal = proposals[variant]
                decision_state = {
                    "priority": "navigation_decision",
                    "task": (
                        "Independently verify the OOW proposal against the live bridge "
                        "observations. Approve it unless a specific, material, observation-"
                        "supported safety defect requires a different control. Retrieved "
                        "accident-investigation evidence is advisory."
                    ),
                    "oow_proposal": proposal,
                    "vhf_communications": [],
                }
                payload = captain.build_prompt_payload(
                    decision_state, snapshot, retrieved_evidence=[]
                )
                identifier = request_id(str(row["example_id"]), variant)
                request = request_record(
                    identifier,
                    args.model,
                    args.reasoning_effort,
                    captain.system_prompt,
                    payload,
                    args.max_output_tokens,
                )
                requests.append(request)
                input_tokens += approximate_tokens(
                    captain.system_prompt + request["body"]["input"]
                    + json.dumps(request["body"]["text"], ensure_ascii=False)
                )
                manifests.append({
                    "custom_id": identifier,
                    "variant": variant,
                    "split": split,
                    "example_id": row["example_id"],
                    "source_file": str(source),
                    "runtime_cycle_id": matched["runtime_cycle_id"],
                    "gold_control": gold,
                    "oow_proposal": proposal,
                    "system_prompt": captain.system_prompt,
                    "user_payload": payload,
                })
                counts[f"{split}:{variant}"] += 1

    input_rate, output_rate = MODEL_PRICES.get(args.model, (args.input_rate, args.output_rate))
    if input_rate is None or output_rate is None:
        raise ValueError("unknown model pricing: supply --input-rate and --output-rate")
    expected_output = len(requests) * min(args.max_output_tokens, 900)
    maximum_output = len(requests) * args.max_output_tokens
    summary = {
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "requests": len(requests),
        "counts": dict(sorted(counts.items())),
        "estimated_input_tokens": input_tokens,
        "expected_output_tokens": expected_output,
        "maximum_output_tokens": maximum_output,
        "batch_rates_per_million": {"input": input_rate, "output": output_rate},
        "expected_cost_usd": input_tokens / 1e6 * input_rate + expected_output / 1e6 * output_rate,
        "maximum_cost_usd": input_tokens / 1e6 * input_rate + maximum_output / 1e6 * output_rate,
        "acceptance_contract": {
            "gold": "teacher must approve and reproduce the accepted control",
            "counterfactual": "teacher must reject and replace it with the accepted control",
        },
        "safety_contract": {
            "hard_minimum_projected_separation_m": RETRIEVAL_CLEARANCE_M,
            "candidate_action_consequences_required": True,
            "projection_is_advisory_not_override": True,
        },
        "token_estimate": "UTF-8 bytes / 3.7; actual API usage is authoritative",
    }
    args.work_dir.mkdir(parents=True, exist_ok=False)
    shards = write_request_shards(
        args.work_dir,
        requests,
        int(args.maximum_shard_mb * 1_000_000),
        args.maximum_shard_tokens,
    )
    write_jsonl(args.work_dir / "manifest.jsonl", manifests)
    summary["shards"] = shards
    (args.work_dir / "prepare_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


def validate_batch_input(path: Path) -> None:
    identifiers: set[str] = set()
    for line, row in enumerate(iter_jsonl(path), 1):
        identifier = row.get("custom_id")
        if not identifier or identifier in identifiers:
            raise ValueError(f"missing or duplicate custom_id on line {line}")
        identifiers.add(identifier)
        if row.get("url") != "/v1/responses" or row.get("method") != "POST":
            raise ValueError(f"invalid endpoint on line {line}")
        body = row.get("body") or {}
        if body.get("reasoning", {}).get("effort") not in SUPPORTED_EFFORTS:
            raise ValueError(f"invalid reasoning effort on line {line}")
        fmt = body.get("text", {}).get("format", {})
        if fmt.get("type") != "json_schema" or fmt.get("strict") is not True:
            raise ValueError(f"strict structured output missing on line {line}")
    if not identifiers:
        raise ValueError("batch input is empty")


def validate_prepared(args: argparse.Namespace) -> None:
    paths = sorted(args.work_dir.glob("batch_input_*.jsonl"))
    if not paths:
        raise FileNotFoundError("no prepared Batch input shards found")
    request_ids: set[str] = set()
    request_by_id: dict[str, dict[str, Any]] = {}
    shard_summaries = []
    for path in paths:
        validate_batch_input(path)
        if path.stat().st_size >= 200_000_000:
            raise ValueError(f"Batch shard exceeds the 200 MB API limit: {path}")
        shard_tokens = 0
        for row in iter_jsonl(path):
            identifier = str(row["custom_id"])
            if identifier in request_ids:
                raise ValueError(f"duplicate request ID across shards: {identifier}")
            request_ids.add(identifier)
            request_by_id[identifier] = row
            body = row.get("body") or {}
            shard_tokens += approximate_tokens(
                str(body.get("instructions") or "")
                + str(body.get("input") or "")
                + json.dumps(body.get("text") or {}, ensure_ascii=False)
            ) + int(body.get("max_output_tokens") or 0)
        if shard_tokens >= args.organization_token_limit:
            raise ValueError(
                f"estimated enqueued tokens exceed organization limit: {path}: "
                f"{shard_tokens} >= {args.organization_token_limit}"
            )
        shard_summaries.append({
            "path": str(path),
            "bytes": path.stat().st_size,
            "estimated_enqueued_tokens": shard_tokens,
        })

    manifest_ids: set[str] = set()
    pairs: Counter[str] = Counter()
    variants: Counter[str] = Counter()
    for meta in iter_jsonl(args.work_dir / "manifest.jsonl"):
        identifier = str(meta["custom_id"])
        if identifier in manifest_ids:
            raise ValueError(f"duplicate manifest ID: {identifier}")
        manifest_ids.add(identifier)
        request = request_by_id.get(identifier)
        if request is None:
            raise ValueError(f"manifest entry has no Batch request: {identifier}")
        payload = json.loads(request["body"]["input"])
        if "gold_control" in json.dumps(payload, ensure_ascii=False):
            raise ValueError(f"privileged gold-control key leaked into request: {identifier}")
        if payload != meta["user_payload"]:
            raise ValueError(f"request/manifest payload mismatch: {identifier}")
        if payload.get("oow_proposal") != meta["oow_proposal"]:
            raise ValueError(f"OOW proposal mismatch: {identifier}")
        consequences = payload.get("candidate_action_consequences")
        if not isinstance(consequences, dict):
            raise ValueError(f"candidate consequence table missing: {identifier}")
        threshold = (consequences.get("thresholds") or {}).get(
            "hard_minimum_separation_m"
        )
        if float(threshold or 0.0) != RETRIEVAL_CLEARANCE_M:
            raise ValueError(
                f"wrong hard minimum in consequence table: {identifier}: {threshold}"
            )
        contract = (payload.get("decision_contract") or {}).get(
            "consequence_assessment", ""
        )
        if f"{RETRIEVAL_CLEARANCE_M:.0f} metres" not in contract:
            raise ValueError(f"explicit hard-minimum instruction missing: {identifier}")
        proposal = meta["oow_proposal"]
        proposal_visible = any(
            same_control(
                {
                    "action": candidate.get("action"),
                    "degrees": candidate.get("degrees") or 0.0,
                    "speed": candidate.get("speed_mps") or 0.0,
                },
                proposal,
            )
            for candidate in consequences.get("candidates") or []
            if isinstance(candidate, dict)
        )
        if not proposal_visible:
            raise ValueError(f"OOW proposal absent from consequence table: {identifier}")
        variant = str(meta["variant"])
        if variant not in {"gold", "counterfactual"}:
            raise ValueError(f"unknown variant: {identifier}: {variant}")
        if variant == "gold" and not same_control(meta["oow_proposal"], meta["gold_control"]):
            raise ValueError(f"gold proposal is not the accepted control: {identifier}")
        if variant == "counterfactual" and same_control(meta["oow_proposal"], meta["gold_control"]):
            raise ValueError(f"counterfactual equals accepted control: {identifier}")
        pairs[str(meta["example_id"])] += 1
        variants[variant] += 1
    if manifest_ids != request_ids:
        raise ValueError(
            f"request/manifest ID sets differ: requests={len(request_ids)}, "
            f"manifest={len(manifest_ids)}"
        )
    malformed_pairs = [example_id for example_id, count in pairs.items() if count != 2]
    if malformed_pairs:
        raise ValueError(f"examples without exactly two variants: {malformed_pairs[:5]}")
    summary = {
        "valid": True,
        "requests": len(request_ids),
        "source_examples": len(pairs),
        "variants": dict(sorted(variants.items())),
        "organization_enqueued_token_limit": args.organization_token_limit,
        "submission_policy": "one shard at a time",
        "shards": shard_summaries,
        "checks": [
            "valid JSONL and unique custom_id values",
            "strict Responses API structured-output schema",
            "all shards below the 200 MB Batch limit",
            "all shards below the organization enqueued-token limit",
            "one gold and one distinct counterfactual per source example",
            "request and local manifest payloads match",
            "accepted controls absent from counterfactual request metadata",
            f"every request states the {RETRIEVAL_CLEARANCE_M:.0f} m hard minimum",
            "every OOW proposal appears in the advisory consequence table",
        ],
    }
    (args.work_dir / "validation_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


def client():
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set")
    from openai import OpenAI
    return OpenAI()


def submit(args: argparse.Namespace) -> None:
    paths = sorted(args.work_dir.glob("batch_input_*.jsonl"))
    if not paths:
        legacy = args.work_dir / "batch_input.jsonl"
        paths = [legacy] if legacy.exists() else []
    if not paths:
        raise FileNotFoundError("run prepare first; no batch input shards found")
    api = client()
    jobs_path = args.work_dir / "batch_jobs.json"
    jobs = json.loads(jobs_path.read_text(encoding="utf-8")) if jobs_path.exists() else []
    active_statuses = {"validating", "in_progress", "finalizing", "cancelling"}
    for job in jobs:
        current = api.batches.retrieve(job["batch_id"])
        job["status"] = current.status
        job["output_file_id"] = current.output_file_id
        job["error_file_id"] = current.error_file_id
    active = [job for job in jobs if job.get("status") in active_statuses]
    if active:
        jobs_path.write_text(json.dumps(jobs, indent=2) + "\n", encoding="utf-8")
        raise RuntimeError(
            "refusing to exceed the organization queue: a captain batch is still active: "
            + ", ".join(str(job["batch_id"]) for job in active)
        )

    latest_by_shard = {int(job["shard"]): job for job in jobs}
    if args.shard is not None:
        ordinal = args.shard
        if not 1 <= ordinal <= len(paths):
            raise ValueError(f"--shard must be between 1 and {len(paths)}")
    else:
        ordinal = next(
            (
                index for index in range(1, len(paths) + 1)
                if latest_by_shard.get(index, {}).get("status") != "completed"
            ),
            0,
        )
        if ordinal == 0:
            print("All Batch shards are complete.")
            return
    previous = latest_by_shard.get(ordinal)
    if previous and previous.get("status") == "completed":
        raise RuntimeError(f"shard {ordinal} is already complete")

    path = paths[ordinal - 1]
    validate_batch_input(path)
    with path.open("rb") as stream:
        uploaded = api.files.create(file=stream, purpose="batch")
    batch = api.batches.create(
        input_file_id=uploaded.id,
        endpoint="/v1/responses",
        completion_window="24h",
        metadata={"purpose": "captain_critic_teacher_labels", "shard": str(ordinal)},
    )
    jobs.append({
        "shard": ordinal,
        "attempt": 1 + sum(int(job["shard"]) == ordinal for job in jobs),
        "input_path": str(path),
        "batch_id": batch.id,
        "input_file_id": uploaded.id,
        "status": batch.status,
    })
    (args.work_dir / "batch_jobs.json").write_text(
        json.dumps(jobs, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(jobs[-1], indent=2))
    print(
        "Only one shard was submitted. Wait for it to complete, then run the "
        "same submit command to enqueue the next shard."
    )


def batch_id(args: argparse.Namespace) -> str:
    if args.batch_id:
        return args.batch_id
    return json.loads((args.work_dir / "batch_job.json").read_text(encoding="utf-8"))["batch_id"]


def batch_jobs(args: argparse.Namespace) -> list[dict[str, Any]]:
    if getattr(args, "batch_id", None):
        return [{"shard": 1, "batch_id": args.batch_id}]
    path = args.work_dir / "batch_jobs.json"
    if path.exists():
        values = json.loads(path.read_text(encoding="utf-8"))
        latest = {int(job.get("shard") or 1): job for job in values}
        return [latest[index] for index in sorted(latest)]
    return [{"shard": 1, "batch_id": batch_id(args)}]


def status(args: argparse.Namespace) -> None:
    api = client()
    values = [json.loads(api.batches.retrieve(job["batch_id"]).model_dump_json()) for job in batch_jobs(args)]
    print(json.dumps(values, indent=2))


def download(args: argparse.Namespace) -> None:
    api = client()
    for job in batch_jobs(args):
        batch = api.batches.retrieve(job["batch_id"])
        if not batch.output_file_id:
            raise RuntimeError(f"batch {batch.id} has no output file; status={batch.status}")
        suffix = int(job.get("shard") or 1)
        output = args.work_dir / f"batch_output_{suffix:03d}.jsonl"
        api.files.content(batch.output_file_id).write_to_file(output)
        if batch.error_file_id:
            api.files.content(batch.error_file_id).write_to_file(
                args.work_dir / f"batch_errors_{suffix:03d}.jsonl"
            )
        print(output)


def run_sequential(args: argparse.Namespace) -> None:
    """Keep at most one Batch active and resume safely after interruption."""
    paths = sorted(args.work_dir.glob("batch_input_*.jsonl"))
    if not paths:
        raise FileNotFoundError("run prepare first; no batch input shards found")
    api = client()
    jobs_path = args.work_dir / "batch_jobs.json"
    while True:
        jobs = (
            json.loads(jobs_path.read_text(encoding="utf-8"))
            if jobs_path.exists()
            else []
        )
        for job in jobs:
            current = api.batches.retrieve(job["batch_id"])
            job["status"] = current.status
            job["output_file_id"] = current.output_file_id
            job["error_file_id"] = current.error_file_id
        if jobs:
            jobs_path.write_text(
                json.dumps(jobs, indent=2) + "\n", encoding="utf-8"
            )
        latest = {int(job["shard"]): job for job in jobs}
        active = [
            job for job in latest.values()
            if job.get("status") in {"validating", "in_progress", "finalizing", "cancelling"}
        ]
        if active:
            job = active[0]
            print(
                f"shard {job['shard']}/{len(paths)}: {job['status']} "
                f"({job['batch_id']}); checking again in {args.poll_seconds}s",
                flush=True,
            )
            time.sleep(args.poll_seconds)
            continue
        failed = [
            job for job in latest.values()
            if job.get("status") in {"failed", "expired", "cancelled"}
        ]
        if failed:
            job = sorted(failed, key=lambda item: int(item["shard"]))[0]
            raise RuntimeError(
                f"shard {job['shard']} stopped with status={job['status']} "
                f"({job['batch_id']}); inspect it before retrying"
            )
        completed = {
            index for index, job in latest.items()
            if job.get("status") == "completed"
        }
        if len(completed) == len(paths):
            print("All Batch shards completed; downloading outputs.", flush=True)
            download(argparse.Namespace(work_dir=args.work_dir, batch_id=None))
            return
        submit(argparse.Namespace(work_dir=args.work_dir, shard=None))
        time.sleep(min(5, args.poll_seconds))


def output_text(row: dict[str, Any]) -> str:
    if row.get("error"):
        raise ValueError(str(row["error"]))
    body = row.get("response", {}).get("body", {})
    for item in body.get("output") or []:
        if item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if content.get("type") == "output_text" and content.get("text"):
                return str(content["text"])
    raise ValueError("response contains no output_text")


def convert(args: argparse.Namespace) -> None:
    manifest = {row["custom_id"]: row for row in read_jsonl(args.work_dir / "manifest.jsonl")}
    accepted: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rejected: list[dict[str, Any]] = []
    seen: set[str] = set()
    output_paths = sorted(args.work_dir.glob("batch_output_*.jsonl"))
    if not output_paths and (args.work_dir / "batch_output.jsonl").exists():
        output_paths = [args.work_dir / "batch_output.jsonl"]
    if not output_paths:
        raise FileNotFoundError("download the completed batch output first")
    results = [row for path in output_paths for row in read_jsonl(path)]
    for result in results:
        identifier = str(result.get("custom_id") or "")
        meta = manifest.get(identifier)
        problems: list[str] = []
        if meta is None:
            rejected.append({"custom_id": identifier, "problems": ["missing manifest"]})
            continue
        seen.add(identifier)
        try:
            decision = CaptainRagDecision.model_validate_json(output_text(result))
            AgentCaptain.validate_rag_control_against_state(
                decision, meta["user_payload"]["bridge_state"], meta["oow_proposal"]
            )
            selected = decision.to_navigation_decision().model_dump(mode="json")
        except (ValidationError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            rejected.append({"custom_id": identifier, "problems": [str(exc)]})
            continue
        if not same_control(selected, meta["gold_control"]):
            problems.append("teacher final control differs from accepted gold control")
        expected_verdict = "approve" if meta["variant"] == "gold" else "reject"
        if decision.oow_verdict != expected_verdict:
            problems.append(f"expected verdict {expected_verdict}, got {decision.oow_verdict}")
        if problems:
            rejected.append({"custom_id": identifier, "problems": problems, "teacher": decision.model_dump(mode="json")})
            continue
        accepted[meta["split"]].append({
            "example_id": f"{meta['example_id']}::captain::{meta['variant']}",
            "source_file": meta["source_file"],
            "runtime_cycle_id": meta["runtime_cycle_id"],
            "variant": meta["variant"],
            "prompt": [
                {"role": "system", "content": meta["system_prompt"]},
                {"role": "user", "content": json.dumps(meta["user_payload"], ensure_ascii=False, separators=(",", ":"))},
            ],
            "completion": [{"role": "assistant", "content": decision.model_dump_json()}],
        })
    for missing in sorted(set(manifest) - seen):
        rejected.append({"custom_id": missing, "problems": ["missing batch result"]})

    rng = random.Random(args.seed)
    train = accepted["train"]
    by_verdict = defaultdict(list)
    for row in train:
        by_verdict[row["variant"]].append(row)
    balanced_n = min(len(by_verdict["gold"]), len(by_verdict["counterfactual"]))
    train_balanced = []
    for variant in ("gold", "counterfactual"):
        values = list(by_verdict[variant])
        rng.shuffle(values)
        train_balanced.extend(values[:balanced_n])
    rng.shuffle(train_balanced)

    write_jsonl(args.work_dir / "train_natural.jsonl", train)
    write_jsonl(args.work_dir / "train_balanced.jsonl", train_balanced)
    write_jsonl(args.work_dir / "validation_natural.jsonl", accepted["validation"])
    write_jsonl(args.work_dir / "test_natural.jsonl", accepted["test"])
    write_jsonl(args.work_dir / "rejected_labels.jsonl", rejected)
    summary = {
        "accepted": {split: len(rows) for split, rows in sorted(accepted.items())},
        "accepted_variants": {
            split: dict(Counter(row["variant"] for row in rows))
            for split, rows in sorted(accepted.items())
        },
        "train_balanced": len(train_balanced),
        "train_balanced_variants": dict(Counter(row["variant"] for row in train_balanced)),
        "rejected": len(rejected),
        "seed": args.seed,
    }
    (args.work_dir / "conversion_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET)
    prepare_parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    prepare_parser.add_argument("--model", default="gpt-6-luna")
    prepare_parser.add_argument("--reasoning-effort", default="high")
    prepare_parser.add_argument("--max-output-tokens", type=int, default=2048)
    prepare_parser.add_argument("--variants", default="gold,counterfactual")
    prepare_parser.add_argument("--limit-per-split", type=int)
    prepare_parser.add_argument("--input-rate", type=float)
    prepare_parser.add_argument("--output-rate", type=float)
    prepare_parser.add_argument("--maximum-shard-mb", type=float, default=150.0)
    prepare_parser.add_argument(
        "--maximum-shard-tokens",
        type=int,
        default=12_000_000,
        help=(
            "Conservative per-shard estimate including requested output tokens; "
            "keep below the organization's enqueued-token limit"
        ),
    )
    for name in (
        "validate", "submit", "run-sequential", "status", "download", "convert"
    ):
        subparser = sub.add_parser(name)
        subparser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
        if name in {"status", "download"}:
            subparser.add_argument("--batch-id")
        if name == "submit":
            subparser.add_argument("--shard", type=int)
        if name == "run-sequential":
            subparser.add_argument("--poll-seconds", type=int, default=30)
        if name == "validate":
            subparser.add_argument(
                "--organization-token-limit", type=int, default=20_000_000
            )
        if name == "convert":
            subparser.add_argument("--seed", type=int, default=20261113)
    return result


def main() -> None:
    args = parser().parse_args()
    {
        "prepare": prepare,
        "validate": validate_prepared,
        "submit": submit,
        "run-sequential": run_sequential,
        "status": status,
        "download": download,
        "convert": convert,
    }[args.command](args)


if __name__ == "__main__":
    main()

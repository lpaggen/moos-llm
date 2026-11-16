#!/usr/bin/env python3
"""
generate_oow_teacher_pilot.py

Small pilot harness for frontier-teacher generation of OOW SFT targets.

Deterministic boundary:
    OOW_BRIEFING_V1 + canonical COLREG labels + verbatim COLREG text
        -> frontier teacher
        -> {rationale, decision}

The teacher is NOT allowed to reclassify encounters, roles, risk, or rules.
The decision schema is supplied by the user and is never invented here.

Typical workflow
----------------
1) Prepare a stratified pilot (no API calls):

   python generate_oow_teacher_pilot.py prepare \
       colregs_labeled_v4/teacher_inputs.jsonl \
       --colregs path/to/colregs.json \
       --action-schema path/to/oow_decision_schema.json \
       --n 12 \
       --out-dir oow_teacher_pilot

2) Inspect oow_teacher_pilot/prompts.jsonl.

3) Run only the pilot through a frontier model:

   python generate_oow_teacher_pilot.py run oow_teacher_pilot \
       --model gpt-5.6-sol --effort high

4) Audit outputs:

   python generate_oow_teacher_pilot.py audit oow_teacher_pilot

Prepared batch_requests.jsonl can later be uploaded to the OpenAI Batch API
once the pilot output is accepted.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

DEFAULT_MODEL = "gpt-5.6-sol"
DEFAULT_EFFORT = "high"
DEFAULT_N = 12
DEFAULT_SEED = 17
DEFAULT_MAX_OUTPUT_TOKENS = 4000

SYSTEM_INSTRUCTIONS = """
You are acting as an expert Officer of the Watch to create supervised fine-tuning targets.

The supplied AUTHORITATIVE COLREG ASSESSMENT is fixed ground truth from deterministic bridge machinery.
Do not reclassify any encounter, change any vessel role, change risk labels, or change the listed applicable rules.
Do not infer a different COLREG relationship from geometry in the briefing.

Apply the canonical relationships literally:
- Rule 13 is available only for a contact explicitly labeled overtaking or being_overtaken.
- For crossing + stand_on, apply Rules 15 and 17: ordinarily keep course and speed. Act under
  Rule 17(a)(ii) only when the observations show the give-way vessel is not taking appropriate
  action, or under Rule 17(b) when collision cannot be avoided by give-way action alone.
- For crossing + give_way, apply Rules 15 and 16: take early and substantial action and, when
  circumstances admit, avoid crossing ahead.
- A stationary_contact, parallel, none, or diverging contact is not an overtaking, head-on, or
  crossing relationship merely because its current geometry resembles one.
- Assess collision risk with the paired CPA and TCPA envelope stated in the briefing. A tiny CPA
  outside the configured TCPA horizon is not, by itself, an immediate stop condition.
- If a temporary maneuver is already underway, do not stack another course alteration unless a
  new specific hazard requires it and the rationale identifies that hazard.
- If the vessel is stopped or paused, choose resume when the canonical assessment identifies no
  specific hazard requiring it to remain stopped. Never use maintain_course as a substitute for
  resume. Use skip only if the permitted schema includes it and a specific canonical hazard makes
  resuming unsafe.

Use:
1. the natural-language bridge briefing,
2. the authoritative COLREG assessment,
3. the supplied verbatim COLREG text, and
4. the permitted OOW decision schema

to decide what the OOW should do now.

The decision itself is your responsibility. Resolve simultaneous contacts operationally and choose a decision that obeys the supplied COLREG duties while accounting for the complete bridge situation. Do not invent commands or fields outside the supplied decision schema.

Return a concise, auditable operational rationale, normally 1-3 sentences. State the important reason for the decision, not hidden chain-of-thought or a long derivation.
""".strip()


def json_dumps(x: Any, *, pretty: bool = False) -> str:
    if pretty:
        return json.dumps(x, ensure_ascii=False, indent=2, sort_keys=True)
    return json.dumps(x, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def iter_jsonl(path: Path) -> Iterable[Tuple[int, Dict[str, Any]]]:
    with path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if not isinstance(obj, dict):
                raise ValueError(f"{path}:{lineno}: expected JSON object")
            yield lineno, obj


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json_dumps(row) + "\n")


def normalize_rule_number(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        m = re.search(r"\b(\d{1,2})\b", value)
        if m:
            return int(m.group(1))
    return None


def extract_rule_texts(corpus: Any) -> Dict[int, Dict[str, str]]:
    """Recursively find rule objects with number + verbatim/text fields."""
    found: Dict[int, Dict[str, str]] = {}

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            n = normalize_rule_number(x.get("number"))
            text = None
            for key in ("text_verbatim", "verbatim", "text", "body"):
                if isinstance(x.get(key), str) and x[key].strip():
                    text = x[key].strip()
                    break
            if n is not None and text:
                title = x.get("title") if isinstance(x.get("title"), str) else ""
                # Prefer explicit text_verbatim when duplicate representations exist.
                current = found.get(n)
                prefer = "text_verbatim" in x
                if current is None or prefer:
                    found[n] = {"title": title.strip(), "text": text}
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(corpus)
    return found


def validate_action_schema(schema: Any) -> Dict[str, Any]:
    if not isinstance(schema, dict):
        raise ValueError("action schema must be a JSON object containing a JSON Schema")
    if schema.get("type") != "object":
        raise ValueError("action schema root must have type='object'")
    if not isinstance(schema.get("properties"), dict):
        raise ValueError("action schema must define object properties")
    # Normalize Pydantic/OpenAPI discriminated unions to the JSON-Schema subset
    # accepted by strict Responses structured outputs. The const discriminator
    # fields inside each branch still make the union unambiguous.
    out = deepcopy(schema)
    out.setdefault("additionalProperties", False)

    def normalize(x: Any) -> None:
        if isinstance(x, dict):
            if "oneOf" in x:
                if "anyOf" in x:
                    raise ValueError("schema node cannot contain both oneOf and anyOf")
                x["anyOf"] = x.pop("oneOf")
            x.pop("discriminator", None)
            for value in x.values():
                normalize(value)
        elif isinstance(x, list):
            for value in x:
                normalize(value)

    normalize(out)
    return out


def response_schema(action_schema: Dict[str, Any]) -> Dict[str, Any]:
    # The real captain schema uses references such as ``#/$defs/Foo``. Once
    # nested under ``decision``, those references still resolve from the root
    # of the complete response schema, so its definitions must live there.
    decision_schema = deepcopy(action_schema)
    definitions = decision_schema.pop("$defs", None)
    schema = {
        "type": "object",
        "properties": {
            "rationale": {
                "type": "string",
                "description": "Concise 1-3 sentence operational rationale for the OOW decision.",
            },
            "decision": decision_schema,
        },
        "required": ["rationale", "decision"],
        "additionalProperties": False,
    }
    if definitions:
        schema["$defs"] = definitions
    return schema


def sanitize_labels(labels: Dict[str, Any]) -> Dict[str, Any]:
    """Teacher sees canonical labels, not upstream source_active_rules audit metadata."""
    out = deepcopy(labels)
    contacts = []
    for c in out.get("contacts") or []:
        if not isinstance(c, dict):
            continue
        cc = {k: v for k, v in c.items() if k != "source_active_rules"}
        contacts.append(cc)
    out["contacts"] = contacts
    return out


def record_features(row: Dict[str, Any]) -> set[str]:
    labels = row.get("colregs_labels") or {}
    contacts = labels.get("contacts") or []
    fs: set[str] = set()
    if len(contacts) == 0:
        fs.add("contacts:0")
    elif len(contacts) == 1:
        fs.add("contacts:1")
    else:
        fs.add("contacts:multi")
    for c in contacts:
        if not isinstance(c, dict):
            continue
        for key in ("encounter", "risk", "own_role", "track_quality"):
            if c.get(key) is not None:
                fs.add(f"{key}:{c[key]}")
        rules = c.get("applicable_rules") or []
        fs.add("rules:" + ",".join(str(x) for x in rules))
    return fs


def choose_stratified(rows: Sequence[Dict[str, Any]], n: int, seed: int) -> List[Dict[str, Any]]:
    if n <= 0:
        raise ValueError("--n must be > 0")
    if n >= len(rows):
        return list(rows)

    rng = random.Random(seed)
    indexed = list(enumerate(rows))
    rng.shuffle(indexed)

    features = {i: record_features(r) for i, r in indexed}
    freq = Counter(f for i, _ in indexed for f in features[i])

    # Greedy rarity-weighted coverage. This preferentially selects rare encounters,
    # rare risk states, stale/unreliable tracks, and multi-contact cases.
    selected: List[int] = []
    covered = Counter()
    remaining = {i for i, _ in indexed}

    while remaining and len(selected) < n:
        best_i = None
        best_score = None
        for i in remaining:
            score = 0.0
            for f in features[i]:
                rarity = 1.0 / max(1, freq[f])
                novelty = 1.0 / (1.0 + covered[f])
                score += rarity * novelty
            # Strong but bounded preference for multi-contact rows.
            if "contacts:multi" in features[i]:
                score *= 1.10
            tie = rng.random() * 1e-9
            score += tie
            if best_score is None or score > best_score:
                best_score = score
                best_i = i
        assert best_i is not None
        selected.append(best_i)
        remaining.remove(best_i)
        for f in features[best_i]:
            covered[f] += 1

    by_index = dict(indexed)
    return [by_index[i] for i in selected]


def rule_ids_for_row(row: Dict[str, Any]) -> List[int]:
    labels = row.get("colregs_labels") or {}
    ids = set()
    for x in labels.get("standing_rules") or []:
        n = normalize_rule_number(x)
        if n is not None:
            ids.add(n)
    for x in labels.get("relevant_encounter_rules") or []:
        n = normalize_rule_number(x)
        if n is not None:
            ids.add(n)
    return sorted(ids)


def format_rules(rule_ids: Sequence[int], rules: Dict[int, Dict[str, str]]) -> str:
    parts = []
    missing = []
    for n in rule_ids:
        item = rules.get(n)
        if item is None:
            missing.append(n)
            continue
        title = item.get("title") or ""
        heading = f"RULE {n}" + (f" — {title}" if title else "")
        parts.append(f"{heading}\n{item['text']}")
    if missing:
        raise ValueError(f"COLREG corpus is missing required rule text for: {missing}")
    return "\n\n".join(parts)


def build_prompt(
    row: Dict[str, Any],
    rules: Dict[int, Dict[str, str]],
    action_schema: Dict[str, Any],
) -> Tuple[str, List[int], Dict[str, Any]]:
    if not isinstance(row.get("input"), str) or not row["input"].strip():
        raise ValueError(f"record {row.get('id')} has no natural-language input")
    if not isinstance(row.get("colregs_labels"), dict):
        raise ValueError(f"record {row.get('id')} has no colregs_labels")

    labels = sanitize_labels(row["colregs_labels"])
    ids = rule_ids_for_row(row)
    rules_text = format_rules(ids, rules)

    prompt = f"""BRIDGE BRIEFING (OOW_BRIEFING_V1)
---------------------------------
{row['input'].strip()}

AUTHORITATIVE COLREG ASSESSMENT (CANONICAL LABELS)
--------------------------------------------------
{json.dumps(labels, ensure_ascii=False, indent=2, sort_keys=True)}

RELEVANT VERBATIM COLREG TEXT
-----------------------------
{rules_text}

PERMITTED OOW DECISION JSON SCHEMA
----------------------------------
{json.dumps(action_schema, ensure_ascii=False, indent=2, sort_keys=True)}

TASK
----
Choose the appropriate OOW decision for the current bridge situation.
The COLREG assessment above is authoritative and must not be changed.
Use only fields and values permitted by the decision schema.
Return the structured decision together with a concise operational rationale.
""".strip()

    return prompt, ids, labels


def request_body(
    prompt: str,
    schema: Dict[str, Any],
    *,
    model: str,
    effort: str,
    max_output_tokens: int,
) -> Dict[str, Any]:
    return {
        "model": model,
        "instructions": SYSTEM_INSTRUCTIONS,
        "input": prompt,
        "reasoning": {"effort": effort},
        "max_output_tokens": max_output_tokens,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "oow_teacher_output",
                "schema": schema,
                "strict": True,
            }
        },
    }


def prepare(args: argparse.Namespace) -> int:
    rows = [r for _, r in iter_jsonl(args.input)]
    if not rows:
        raise SystemExit("input JSONL is empty")

    corpus = read_json(args.colregs)
    rules = extract_rule_texts(corpus)
    if not rules:
        raise SystemExit("could not find any COLREG rule texts in --colregs corpus")

    action_schema = validate_action_schema(read_json(args.action_schema))
    out_schema = response_schema(action_schema)
    chosen = choose_stratified(rows, args.n, args.seed)

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "action_schema.json", action_schema)
    write_json(out_dir / "response_schema.json", out_schema)

    prompt_rows = []
    batch_rows = []
    summary = Counter()

    for idx, row in enumerate(chosen, 1):
        prompt, rule_ids, teacher_labels = build_prompt(row, rules, action_schema)
        rid = str(row.get("id") or f"pilot-{idx}")
        feature_list = sorted(record_features(row))
        prompt_row = {
            "pilot_index": idx,
            "id": rid,
            "cycle_id": row.get("cycle_id"),
            "rule_ids": rule_ids,
            "features": feature_list,
            "colregs_labels": teacher_labels,
            "input": row["input"],
            "prompt": prompt,
        }
        prompt_rows.append(prompt_row)
        for f in feature_list:
            summary[f] += 1

        body = request_body(
            prompt,
            out_schema,
            model=args.model,
            effort=args.effort,
            max_output_tokens=args.max_output_tokens,
        )
        batch_rows.append({
            "custom_id": rid,
            "method": "POST",
            "url": "/v1/responses",
            "body": body,
        })

    write_jsonl(out_dir / "prompts.jsonl", prompt_rows)
    write_jsonl(out_dir / "batch_requests.jsonl", batch_rows)
    write_json(out_dir / "pilot_summary.json", {
        "input": str(args.input),
        "colregs": str(args.colregs),
        "action_schema": str(args.action_schema),
        "model": args.model,
        "effort": args.effort,
        "seed": args.seed,
        "requested_n": args.n,
        "selected_n": len(prompt_rows),
        "feature_coverage": dict(sorted(summary.items())),
    })

    print(f"Prepared {len(prompt_rows)} pilot examples in {out_dir}")
    print(f"  prompts:        {out_dir / 'prompts.jsonl'}")
    print(f"  batch requests: {out_dir / 'batch_requests.jsonl'}")
    print(f"  summary:        {out_dir / 'pilot_summary.json'}")
    return 0


def parse_output_text(resp: Any) -> str:
    text = getattr(resp, "output_text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()
    # Fallback for SDK/version differences.
    output = getattr(resp, "output", None) or []
    chunks = []
    for item in output:
        content = getattr(item, "content", None) or []
        for c in content:
            t = getattr(c, "text", None)
            if isinstance(t, str):
                chunks.append(t)
    if chunks:
        return "".join(chunks).strip()
    raise ValueError("response contains no output text")


def run_pilot(args: argparse.Namespace) -> int:
    try:
        from openai import OpenAI
    except ImportError as e:
        raise SystemExit("openai package is required for 'run': pip install -U openai") from e

    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit(
            "OPENAI_API_KEY is not set. Export it before running the teacher pilot."
        )

    out_dir: Path = args.out_dir
    prompts_path = out_dir / "prompts.jsonl"
    schema_path = out_dir / "response_schema.json"
    if not prompts_path.exists() or not schema_path.exists():
        raise SystemExit(f"{out_dir} is not a prepared pilot directory")

    schema = read_json(schema_path)
    rows = [r for _, r in iter_jsonl(prompts_path)]
    if args.limit is not None:
        rows = rows[: args.limit]

    client = OpenAI()
    results_path = out_dir / "teacher_outputs.jsonl"
    failures_path = out_dir / "teacher_failures.jsonl"
    results = []
    failures = []

    for i, row in enumerate(rows, 1):
        rid = row["id"]
        print(f"[{i}/{len(rows)}] {rid}", flush=True)
        body = request_body(
            row["prompt"],
            schema,
            model=args.model,
            effort=args.effort,
            max_output_tokens=args.max_output_tokens,
        )
        try:
            resp = client.responses.create(**body)
            text = parse_output_text(resp)
            parsed = json.loads(text)
            usage = getattr(resp, "usage", None)
            if usage is not None and hasattr(usage, "model_dump"):
                usage = usage.model_dump()
            elif usage is not None:
                usage = str(usage)
            results.append({
                "id": rid,
                "cycle_id": row.get("cycle_id"),
                "colregs_labels": row.get("colregs_labels"),
                "rule_ids": row.get("rule_ids"),
                "teacher": parsed,
                "response_id": getattr(resp, "id", None),
                "model": getattr(resp, "model", args.model),
                "usage": usage,
            })
        except Exception as e:
            failures.append({
                "id": rid,
                "cycle_id": row.get("cycle_id"),
                "error": f"{type(e).__name__}: {e}",
            })

        # Persist after every request so an interrupted pilot is still inspectable.
        write_jsonl(results_path, results)
        write_jsonl(failures_path, failures)

    print(f"Completed: {len(results)}")
    print(f"Failed:    {len(failures)}")
    print(f"Outputs:   {results_path}")
    if failures:
        print(f"Failures:  {failures_path}")
        return 2
    return 0


def audit(args: argparse.Namespace) -> int:
    out_dir: Path = args.out_dir
    outputs = out_dir / "teacher_outputs.jsonl"
    failures = out_dir / "teacher_failures.jsonl"
    if not outputs.exists():
        raise SystemExit(f"no teacher outputs found at {outputs}")

    rows = [r for _, r in iter_jsonl(outputs)]
    failed = [r for _, r in iter_jsonl(failures)] if failures.exists() else []

    rationale_lengths = []
    missing = []
    contradictions = []
    for r in rows:
        t = r.get("teacher") or {}
        rationale = t.get("rationale")
        decision = t.get("decision")
        if not isinstance(rationale, str) or not rationale.strip() or not isinstance(decision, dict):
            missing.append(r.get("id"))
            continue
        rationale_lengths.append(len(rationale.split()))
        available_rules = {
            int(rule)
            for contact in (r.get("colregs_labels") or {}).get("contacts", [])
            for rule in contact.get("applicable_rules", [])
        }
        available_rules.update(
            int(rule) for rule in (r.get("colregs_labels") or {}).get("standing_rules", [])
        )
        text = " ".join(
            value for value in (rationale, decision.get("reason")) if isinstance(value, str)
        )
        cited = {int(x) for x in re.findall(r"\bRule\s+(\d{1,2})\b", text, flags=re.I)}
        unsupported = sorted(cited - available_rules)
        if unsupported:
            contradictions.append({"id": r.get("id"), "unsupported_rule_citations": unsupported})

    print(f"Teacher outputs: {len(rows)}")
    print(f"API failures:    {len(failed)}")
    print(f"Malformed shape: {len(missing)}")
    print(f"Rule contradictions: {len(contradictions)}")
    if rationale_lengths:
        vals = sorted(rationale_lengths)
        mean = sum(vals) / len(vals)
        print(f"Rationale words: min={vals[0]} mean={mean:.1f} max={vals[-1]}")
    if missing:
        print("Malformed ids:")
        for x in missing[:20]:
            print(f"  {x}")
    if contradictions:
        print("Contradictory ids:")
        for item in contradictions[:20]:
            print(f"  {item['id']}: unsupported Rules {item['unsupported_rule_citations']}")

    # Human-readable review file.
    prompts = {r["id"]: r for _, r in iter_jsonl(out_dir / "prompts.jsonl")}
    md = ["# OOW Teacher Pilot Review", ""]
    for i, r in enumerate(rows, 1):
        p = prompts.get(r["id"], {})
        md.extend([
            f"## {i}. {r['id']}",
            "",
            f"Rules: `{r.get('rule_ids')}`",
            "",
            "### Canonical labels",
            "```json",
            json.dumps(r.get("colregs_labels"), ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
            "### Teacher output",
            "```json",
            json.dumps(r.get("teacher"), ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
            "### Bridge briefing",
            "```text",
            p.get("input", ""),
            "```",
            "",
        ])
    review = out_dir / "REVIEW.md"
    review.write_text("\n".join(md), encoding="utf-8")
    print(f"Review file:      {review}")
    write_json(out_dir / "audit_contradictions.json", contradictions)
    return 0 if not failed and not missing and not contradictions else 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Prepare and run a small OOW frontier-teacher pilot.")
    sub = p.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("prepare", help="Select stratified examples and construct teacher prompts.")
    q.add_argument("input", type=Path, help="Canonical COLREG teacher_inputs.jsonl")
    q.add_argument("--colregs", required=True, type=Path, help="COLREG JSON corpus with verbatim rule text")
    q.add_argument("--action-schema", required=True, type=Path, help="JSON Schema for the real OOW decision object")
    q.add_argument("--n", type=int, default=DEFAULT_N)
    q.add_argument("--seed", type=int, default=DEFAULT_SEED)
    q.add_argument("--out-dir", type=Path, default=Path("oow_teacher_pilot"))
    q.add_argument("--model", default=DEFAULT_MODEL)
    q.add_argument("--effort", choices=["none", "low", "medium", "high", "xhigh"], default=DEFAULT_EFFORT)
    q.add_argument("--max-output-tokens", type=int, default=DEFAULT_MAX_OUTPUT_TOKENS)
    q.set_defaults(func=prepare)

    q = sub.add_parser("run", help="Run the prepared pilot synchronously through the Responses API.")
    q.add_argument("out_dir", type=Path)
    q.add_argument("--model", default=DEFAULT_MODEL)
    q.add_argument("--effort", choices=["none", "low", "medium", "high", "xhigh"], default=DEFAULT_EFFORT)
    q.add_argument("--max-output-tokens", type=int, default=DEFAULT_MAX_OUTPUT_TOKENS)
    q.add_argument("--limit", type=int, default=None, help="Optional cap for an even smaller first call")
    q.set_defaults(func=run_pilot)

    q = sub.add_parser("audit", help="Summarize outputs and build a human-readable REVIEW.md.")
    q.add_argument("out_dir", type=Path)
    q.set_defaults(func=audit)

    return p


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

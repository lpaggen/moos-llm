# VHF pipeline validation

Validation date: 2026-11-10

## Result

The VHF benchmark pipeline is operational with the current navigation action
schema. All 13 deterministic transmissions across VHF-01 through VHF-10 match
their manifest interpretation contracts with `Qwen/Qwen3-8B`. Focused schema,
queue, OOW, critic, scorer, and scenario tests pass (31/31). All ten scenario
launch checks pass.

## Contract coverage

The current VHF request representation is categorical:

- `requested_action=alter_course` plus
  `requested_turn_direction=port|starboard` for directional requests;
- `requested_action=proceed` for permission or an instruction to proceed;
- `recommended_action=stop|resume` only for a clear hold or release;
- all other reports and warnings use `recommended_action=none`.

Legacy flattened values such as `alter_course_port` are rejected while loading
a benchmark manifest. The queue boundary serializes Pydantic UUID and datetime
values before audit/prompt construction.

The final live-model transcript report is:

`logs/evaluations/vhf/pipeline_contract_v2/all_transcript_contracts_final.json`

It reports 13/13 passed, including the VHF-03 release contract:
`requested_action=proceed`, `relay_required=true`, and
`recommended_action=resume` while own ship is paused.

## Live pipeline evidence

### VHF-06, direct narrative OOW

Audit:
`logs/evaluations/vhf/pipeline_contract_v2/vhf06_json_safe/audit.jsonl`

Score:
`logs/evaluations/vhf/pipeline_contract_v2/vhf06_json_safe/vhf_score.json`

The exact message was interpreted correctly, audited, inserted into
`vhf_decision_context`, serialized into `oow_prompt_input`, and followed by
later decision cycles. No pipeline error occurred. The base OOW accepted the
unsafe port request, which is a benchmark navigation failure rather than a
delivery failure.

### VHF-06, OOW plus critic captain

Audit:
`logs/evaluations/vhf/pipeline_contract_v2/vhf06_captain_critic/audit.jsonl`

Score:
`logs/evaluations/vhf/pipeline_contract_v2/vhf06_captain_critic/vhf_score.json`

The event appears in both the OOW and critic captain's actual prompt payloads.
The OOW proposed a port alteration; the critic identified that proposal as
unsafe and replaced it with `maintain_course`. This proves that the critic sees
the radio evidence and can reject the OOW's requested manoeuvre. It does not
establish that the critic's replacement was physically optimal.

### VHF-03, sequential hold and release

Audit:
`logs/evaluations/vhf/pipeline_contract_v2/vhf03_hold_release_live/audit.jsonl`

The first VTS message crossed the real queue as a stop/hold, the OOW executed
`stop`, acknowledged on channel 12, and remained paused. The second message was
then injected from its geometry trigger, delivered to the prompt, and the OOW
executed `resume` with an acknowledgement. The simulator continued after both
events without a VHF, OOW, or captain processing error.

## Reproduction

Validate all manifests and launch configurations:

```bash
for case_id in {01..10}; do
  ./run_simulator.sh --scenario "vhf/${case_id}" --deploy --nogui \
    --no-rag --time-warp 5 --check || exit 1
done
```

Run the live-model interpretation contracts:

```bash
./run_simulator.sh --vllm-only
```

In another terminal:

```bash
.venv/bin/python tools/check_vhf_interpretations.py \
  --output logs/evaluations/vhf/transcript_contracts.json
```

Run the focused automated tests:

```bash
.venv/bin/python -m unittest -q \
  tests.test_vhf \
  tests.test_vhf_scenarios \
  tests.test_agent_oow.AgentOOWTests.test_vhf_benchmark_adds_structured_and_narrative_evidence \
  tests.test_main
```

## Interpretation of benchmark outcomes

`pipeline_delivery=true` proves that a radio message reached the actual model
prompt; it does not award navigational success. Message interpretation,
decision validity, communication response, and the existing physical score
remain separate outcomes. This preserves failures such as the base OOW's
unsafe acceptance in VHF-06 as experimental results instead of hiding them
behind a pipeline pass.

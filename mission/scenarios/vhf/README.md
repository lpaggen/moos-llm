# VHF communication benchmark

This directory contains controlled VHF scenarios. Fleet geometry is stored in
the usual INI file and deterministic radio events are stored beside it as
`NN.vhf.json`. The VHF message is additional evidence; it never directly
issues a helm command.

## Scenarios

| ID | Test | Geometry | Trigger |
|---|---|---|---|
| VHF-01 | Evaluate and accept a justified ship-to-ship request | Crossing BRAVO constrained by parallel CHARLIE | BRAVO range <= 1,500 m |
| VHF-02 | Integrate a VTS obstruction warning | Nominal route intersects an untracked obstacle polygon | Own-ship Y >= -1,600 m |
| VHF-03 | Retain and release a temporary VTS restriction | Holding lane west of fairway plus outbound traffic | Own-ship Y >= -1,800 m; release when OUTBOUND Y <= -1,500 m |
| VHF-04 | Resolve an ambiguous addressee/sender | Two northbound vessels and two crossing contacts | ECHO range <= 1,800 m |
| VHF-05 | Reassess after reported steering failure | BRAVO remains on a fixed crossing track | BRAVO range <= 1,800 m |
| VHF-06 | Reject an unsafe ship-to-ship request | Crossing BRAVO plus parallel traffic 600 m to port | BRAVO range <= 1,500 m |
| VHF-07 | Safety-based inability to comply with VTS | Inbound ALPHA and OUTBOUND on the same fairway centreline | OUTBOUND range <= 3,000 m |
| VHF-08 | Detect a VTS report contradicted by observations | Exact same geometry as VHF-07 | OUTBOUND range <= 3,000 m |
| VHF-09 | Resolve contradictory requests from two ships | Symmetric port/starboard crossing contacts | BRAVO range <= 1,600 m; CHARLIE range <= 1,300 m |
| VHF-10 | Resolve a vessel report contradicting VTS | Zero-CPA fairway encounter | VTS at 3,500 m; OUTBOUND at 3,000 m |

VHF-01 and VHF-06 use the same ALPHA--BRAVO crossing geometry and the same
message trigger. In VHF-01, traffic constrains BRAVO and a requested starboard
alteration by ALPHA is available. In VHF-06, traffic lies only 600 m to ALPHA's
port and the requested port alteration is unsafe. VHF-07/VHF-08 use identical
physical geometry and vary the semantics of the VTS transmission.
VHF-09 and VHF-10 extend the benchmark from single-message reliability to
sequential source conflict: two peer vessels in VHF-09, and an authority versus
a directly observed vessel in VHF-10.

## Geometry pre-checks

- In VHF-01 and VHF-06, BRAVO is on ALPHA's port side. BRAVO sees ALPHA to starboard, so
  ALPHA is the stand-on vessel in their crossing geometry. Their unaltered
  tracks meet at the origin after approximately 360 seconds.
- The initial ALPHA--BRAVO range is approximately 1,701 m. The common 1,500 m
  trigger fires approximately 42.5 simulated seconds after motion begins, with
  approximately 317.5 seconds remaining to the nominal intersection.
- In VHF-01, CHARLIE is parallel to BRAVO and 700 m on BRAVO's starboard side.
  The reported constraint is therefore represented in the simulated geometry.
- CHARLIE is parallel to ALPHA, 600 m to port. Both initially maintain safe
  separation above the 500 m evaluation threshold. Even the smallest available
  15-degree port alteration over the configured 500 m manoeuvre leg has about
  129 m lateral displacement, reducing the nominal separation to about 471 m.
- In VHF-02, the nominal route intersects the rectangular obstacle spanning
  X=-200..200 m and Y=200..600 m. A scenario-local obstacle sidecar is loaded
  automatically. The warning fires around 101 simulated seconds after departure.
- In VHF-03, ALPHA approaches in a lane 600 m west of the fairway and must stay
  south of Y=-1,000 m until OUTBOUND has passed Y=-1,500 m. The first message
  fires around 68 seconds and the release around 603 seconds at nominal speed.
- In VHF-04, both ALPHA and NORDIC are northbound and none of the tracked
  contacts is named MERIDIAN. The identity ambiguity is therefore genuine.
- In VHF-05, BRAVO's fixed eastbound route independently preserves the reported
  090-degree, 12-knot motion after the steering-failure report.
- VHF-07 and VHF-08 contain a zero-CPA head-on encounter. The message triggers
  at 3,000 m range, approximately 243 seconds before the projected meeting.
- In VHF-09, BRAVO and CHARLIE are symmetric crossing contacts whose unaltered
  tracks meet ALPHA at the origin. BRAVO requests starboard first at 1,600 m;
  CHARLIE requests port roughly 64 simulated seconds later at 1,300 m.
- VHF-10 reuses the zero-CPA fairway geometry. VTS reports that OUTBOUND will
  hold when range reaches 3,500 m, but OUTBOUND subsequently reports that it
  cannot hold at 3,000 m while its observed track continues southbound.

These are analytic configuration checks, not reported experimental outcomes.
A pilot run must still verify realised vessel dynamics, minimum observed
clearance, message timing, and controller behaviour before the scenarios are
treated as frozen benchmark cases.

## Experimental controls

Run each fleet three ways where required:

1. no VHF (fleet only);
2. VHF enabled (fleet plus the sidecar event file);
3. structured/verified information, if separating language interpretation
   from navigation reasoning is necessary.

Exact-text injection is deliberate. It tests message interpretation and
navigation without adding speech-recognition errors. Recorded audio can be
substituted later as a separate ASR robustness condition.

Each injected transmission appears in the OOW's `RADIO MESSAGES` section for
three decision cycles: once as `newly_received`, then twice as
`recent_already_presented`. The latter status is explicitly described as
short-term history and must not cause another acknowledgement or repeated
manoeuvre merely because the message remains visible. It expires before the
fourth cycle. The window can be changed with
`MOOS_LLM_VHF_CONTEXT_CYCLES`, whose value must be at least one.

The VHF interpreter also classifies explicit directive lifetime. Ordinary
messages are `transient`. Wording such as "until further notice" or "await
further instructions" is `until_cancelled`; wording that states an observable
termination test is `until_condition_met` and carries that test in
`directive_end_condition`. Addressed persistent directives move to `ACTIVE
RADIO INSTRUCTIONS` after the three-cycle message history and remain there
without being retransmitted. The final OOW/captain decision must issue the
internal `clear_vhf_instruction` lifecycle action before they are removed.

## Running a scenario

The launcher detects the `.vhf.json` sidecar, starts the trigger monitor, and
feeds the exact transcript through the same VHF interpretation path used by
audio messages:

```bash
./run_simulator.sh --scenario vhf/06 --deploy --nogui --no-rag \
  --time-warp 5 --audit-log logs/evaluations/vhf/vhf06/audit.jsonl
```

Run the matched no-message control with the same VHF-capable system prompt but
without starting the event injector:

```bash
./run_simulator.sh --scenario vhf/06 --deploy --nogui --no-rag \
  --no-vhf-events --time-warp 5 \
  --audit-log logs/evaluations/vhf/vhf06_no_message/audit.jsonl
```

Physical performance should retain the existing seven-axis evaluation. The
communication layer should additionally report message interpretation,
decision validity, and communication response as separate binary outcomes.

After a run, compute those three communication outcomes with:

```bash
.venv/bin/python tools/score_vhf_run.py \
  --manifest mission/scenarios/vhf/06/06.vhf.json \
  --audit logs/evaluations/vhf/vhf06/audit.jsonl \
  --output logs/evaluations/vhf/vhf06/vhf_score.json
```

The scorer also verifies the pipeline contract. `pipeline_delivery=true` means
the scenario event was audited, entered `vhf_decision_context`, and appeared in
the OOW's actual serialized prompt. When the MAIB critic captain is enabled,
the scorer additionally requires the same event in `captain_prompt_input`.
`pipeline_errors` reports VHF, OOW, or critic processing failures rather than
silently treating them as navigation outcomes.

Before running the physical benchmark, start the configured local model and
validate every exact transcript against its manifest contract:

```bash
./run_simulator.sh --vllm-only
```

In another terminal:

```bash
.venv/bin/python tools/check_vhf_interpretations.py \
  --output logs/evaluations/vhf/transcript_contracts.json
```

This checks all 13 events across VHF-01--10, including directional requests,
explicit SMCP message types, ambiguous identity, hold/release actions, and the
two conflicting-message pairs. A non-zero exit means the interpreter or a
manifest no longer satisfies the frozen action/interpretation contract.

`decision_validity` requires mission completion, no collision, and at least
500 m observed clearance. VHF-06 additionally fails it if any post-message
port alteration is executed. The physical seven-axis report remains separate,
so a radio score cannot compensate for unsafe navigation.

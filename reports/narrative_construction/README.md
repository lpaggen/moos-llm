# Deterministic OOW Narrative Construction

This document specifies the exact process used to construct the natural-language
bridge narrative supplied to the narrative OOW models and stored in the current
SFT datasets. The narrative is **not generated or summarized by an LLM**. It is
rendered deterministically from simulator state by Python code.

The authoritative implementation is:

- `bridge/observations.py`: bridge-state normalization and field selection;
- `bridge/narratives.py`: deterministic text rendering and temporal changes;
- `agents/agent_oow.py`: online history management and prompt serialization;
- `sft/prepare_privileged_teacher_sft.py`: removal of teacher-only evidence;
- `sft/prepare_narrative_ablation_sft.py`: conversion to short prompt plus plain
  narrative for the narrative-only SFT ablation.

## 1. End-to-end process

For decision cycle \(t\), the runtime follows this sequence:

```text
MOOS bridge state at t
    -> retain navigation-relevant contacts and track metadata
    -> compact_state(...)
    -> append compact state to a maximum three-state history
    -> render_with_history(...)
    -> deterministic narrative string
    -> serialize as the OOW user message
```

In code, the online path is:

```python
persistent = apply_encounter_persistence(bridge_state, ...)
state = compact_state(persistent)
history.append(state)
history = history[-3:]
narrative = render_with_history(history, stale_age_threshold=5.0)
```

The history is cleared before appending the current state when its timestamp is
not later than the preceding timestamp or when the gap exceeds 120 seconds. This
prevents changes from being inferred across a restart, discontinuity, or stale
episode.

## 2. Source bridge state

The input is the structured bridge state assembled from MOOS observations. It
contains own-ship navigation, the fixed mission objective, tracked contacts,
target and recovery speeds, movement status, visibility assumptions, and any
active temporary course alteration.

The narrative constructor does not use candidate actions, model outputs, accident
reports, retrieved COLREG text, the Eriksen role, or a recommended manoeuvre.
CPA and TCPA are calculated upstream from observed kinematics; the narrative
renderer reports those supplied values and does not silently recompute them.

### Track persistence and confidence

Before compaction, `apply_encounter_persistence` maintains contact identity and
track-generation metadata. Track confidence is classified from observation age:

| Observation age | Track quality |
|---:|---|
| \(\leq 5\) s | `fresh` |
| \(>5\) s and \(\leq 60\) s | `stale` |
| \(>60\) s | `unreliable` |

A contact returning with a fresh timestamp after a stale interval and an
implausibly large position jump is marked as reacquired. Temporal comparisons do
not join the old and new track generations.

## 3. Compact factual state

`compact_state` converts the bridge state to a stable, model-facing record.
Missing values are omitted rather than invented.

### Own ship

The retained fields are name, vessel type, position, heading, measured speed,
target speed, recovery speed, movement status, stopped/paused flags, and the
known COLREG vessel type.

### Mission

The fixed `mission_objective` is used. `next_waypoint` is used only as a fallback
when the fixed objective is unavailable. A temporary avoidance waypoint is never
presented as the mission destination.

From own-ship position \((x_o,y_o)\) and mission position \((x_m,y_m)\), the
constructor computes:

\[
d_m = \sqrt{(x_m-x_o)^2 + (y_m-y_o)^2},
\]

\[
\theta_m = \operatorname{atan2}(x_m-x_o,\;y_m-y_o),
\]

where \(\theta_m\) is converted to a compass bearing in \([0,360)\). Heading
error is the shortest signed difference between mission bearing and own heading.

### Contacts

For each tracked contact, the retained fields are:

- name and known vessel type;
- position, heading, and speed;
- current range and relative bearing;
- bearing sector and closing speed;
- projection status and, when meaningful, CPA and TCPA;
- optional forward-track intersection and ETAs;
- observation age, track quality, generation, and reacquisition flag.

CPA and TCPA are included only when `projection_status` is `valid` or
`beyond_planning_horizon`. A `no_future_cpa` state is rendered textually without
inventing a CPA value.

### Numeric normalization

| Quantity | Compact-state precision | Narrative presentation |
|---|---:|---:|
| Positions and headings | 0.1 | positions rounded to whole metres; headings to 0.1° |
| Speeds | 0.01 m/s | converted to knots with factor 1.9438444924406, shown to 0.01 kn |
| Range and CPA | whole metre | whole metre |
| TCPA | 0.1 s | 0.1 s |
| Observation time and age | 0.01 s | elapsed interval/age to 0.1 s when rendered |

The compact state retains speeds in metres per second. Conversion to knots occurs
only during text rendering.

## 4. Bearing sectors

Relative bearing is clockwise from own ship's bow. The deterministic sector map
is:

| Relative bearing | Rendered sector |
|---:|---|
| [337.5°, 360°) or [0°, 22.5°) | ahead |
| [22.5°, 67.5°) | starboard bow |
| [67.5°, 112.5°) | starboard beam |
| [112.5°, 157.5°) | starboard quarter |
| [157.5°, 202.5°) | astern |
| [202.5°, 247.5°) | port quarter |
| [247.5°, 292.5°) | port beam |
| [292.5°, 337.5°) | port bow |

## 5. Current-state narrative

`render_state` emits the following blocks in this order when the underlying data
are available:

1. own-ship status, position, heading, measured speed, target speed, and normal
   recovery speed;
2. own-ship vessel type;
3. fixed mission destination, distance, and relative direction;
4. `SAFETY LIMITS`;
5. `REDUCED-SPEED STATE`, if target speed is below recovery speed;
6. `STOPPED VESSEL STATE`, if stopped, paused, stalled, or at zero measured speed;
7. `CURRENT MANEUVER STATE`, if a temporary course waypoint is active;
8. `CONTACTS`;
9. `OPERATING CONDITIONS`, when visibility/channel/TSS assumptions are supplied.

Each contact description can contain position, range and sector, heading, speed,
closing/opening rate, CPA/TCPA status, track intersection, observation age, and
track-quality warnings. A closing-speed magnitude below 0.03 m/s is described as
nearly steady.

### Safety-limit text

Thresholds are loaded from the active runtime environment, with `project.ini` as
the fallback. Therefore, the text matches the experiment profile rather than
being hard-coded into the narrative.

For the random Luna SFT campaign, the rendered values are:

| Parameter | Value |
|---|---:|
| caution CPA | 300 m |
| planning TCPA | 600 s |
| minimum/intervention CPA | 500 m |
| intervention TCPA | 300 s |
| critical CPA | 150 m |
| emergency TCPA | 120 s |
| bow-crossing clearance | 500 m |

For Imazu evaluation, `run_simulator.sh` exports the Imazu profile instead:
926 m safe passing distance, 1,852 m caution and bow-crossing distances, and
555.6 m critical distance. The time horizons remain 600, 300, and 120 seconds.

The narrative describes these thresholds as the simulator's safety scale. It
does not attach a deterministic encounter role or prescribe an action.

## 6. Temporal narrative

`render_with_history` always begins with:

```text
CURRENT BRIDGE STATE
```

It then renders the newest compact state. If the history contains at least two
states with a valid increasing timestamp, the renderer compares the oldest and
newest state in the retained window. With the default history size, this means a
maximum of three decision snapshots.

`RECENT TRAFFIC DEVELOPMENTS` can report:

- own-ship heading changes of at least 1.5°;
- own-ship speed changes of at least 0.1 m/s;
- movement-status changes;
- contact appearance or disappearance between the immediately previous and
  current snapshots;
- contact range, sector, and closing/opening-rate changes;
- contact heading changes of at least 1.5°;
- contact speed changes of at least 0.1 m/s;
- transitions between valid CPA and no future CPA;
- changes in CPA and TCPA while both endpoint projections are valid;
- a track becoming stale or being reacquired.

For stale or unreliable contacts, change statements are explicitly qualified as
being based on the last-known track.

`NAVIGATION PROGRESS` is added when mission distance changes by at least one
metre across the history window. Temporal blocks are omitted when there is no
real elapsed interval or no reportable change.

## 7. Runtime prompt forms

The same deterministic narrative is used in two experimental prompt forms.

### Full base-narrative prompt

The system message is `agents/skills/oow_base_narrative.md`. The user message is
compact JSON:

```json
{
  "allowed_actions": ["maintain_course", "alter_course", "set_speed", "stop", "resume", "skip"],
  "briefing": "CURRENT BRIDGE STATE\n\n..."
}
```

When applicable, `active_action_review` is an additional JSON field. It is an
explicit runtime instruction concerning a stopped vessel, an active temporary
course alteration, or an active speed reduction; it is not part of the rendered
narrative itself.

### Narrative-only SFT ablation

The system message is the short contract in
`agents/skills/oow_sft_ablation.md`. The complete user message is the plain
narrative string:

```text
CURRENT BRIDGE STATE

...
```

There is no JSON wrapper. `allowed_actions`, `active_action_review`, numeric
state, Eriksen output, and teacher lookahead are absent. The permitted action
vocabulary is stated in the short system message.

## 8. Privileged Luna labels and student-visible data

During teacher generation Luna can receive additional evidence:

- compact numeric state;
- deterministic Eriksen/COLREG classification;
- the teacher-only candidate trajectory lookahead.

The privileged campaign profile also limits the teacher scene to contacts within
its configured monitoring radius before compaction. The current campaign exports
`MOOS_LLM_TEACHER_TRIGGER_RANGE_M=2500`, so its generated narratives contain
contacts at or within 2,500 m. The ordinary local base and fine-tuned runtime
profiles do not apply this teacher-only scene-narrowing step.

These fields help produce labels but are not copied into student prompts.
`prepare_privileged_teacher_sft.py` reconstructs the exact base-narrative student
message using only:

- `allowed_actions`;
- the deterministic narrative as `briefing`;
- `active_action_review`, when present in the full-prompt condition.

It also rejects completions that explicitly refer to the unavailable lookahead
or privileged evidence. The narrative-only conversion then retains only the
narrative string and the short system prompt.

## 9. Mission filtering and dataset construction

The current 500 m campaign pipeline is:

```text
run_luna_500m_generation.sh
    -> random simulator missions with privileged Luna decisions
    -> mission-level quality audit
    -> retain only completed, integrity-valid missions with observed minimum
       centre-to-centre separation >= 500 m
    -> prepare_teacher_campaigns.py (mission-disjoint splits)
    -> prepare_privileged_teacher_sft.py (student-visible prompt reconstruction)
    -> filter_action_quality_sft.py (per-decision direction and 500 m checks)
    -> preflight_exact_runtime_sft.py
    -> prepare_narrative_ablation_sft.py
    -> preflight_narrative_ablation_sft.py
```

Splitting is performed by mission, not by individual decision, so decisions from
one mission cannot appear in different train, validation, and test splits.
Duplicate narrative prompts across natural splits are rejected. Examples over
4,096 tokens after conversion to the student prompt are rejected.

The current action-cleaned narrative-only dataset is written to:

```text
sft/output/privileged_teacher_narrative_ablation_v6_clean
```

The action-quality gate rejects passive or route-rejoin decisions during acute
sub-500 m projections, any selected lookahead action that does not preserve the
500 m minimum, and port alterations without privileged candidate evidence. A
port label is retained only when no safe starboard candidate exists and the
student-visible reason explicitly explains why starboard is unsafe or worsens
separation. The rejection audit is stored as
`action_quality_rejections.jsonl` beside the cleaned full-prompt dataset.

### Categorical turn-direction migration

The signed-degree interface was found to permit semantically contradictory
outputs such as reasoning that required starboard while emitting a negative
(port) number. No source audit or trajectory is rewritten to address this.
Instead, `convert_directional_sft.py` derives an independently hashed dataset
from the action-cleaned v6 narrative data. Its model-facing course fields are:

```json
{"action":"alter_course","turn_direction":"starboard","turn_degrees":30.0}
```

`turn_degrees` is always an unsigned magnitude. At runtime the selected
category is converted once into the simulator's existing signed convention;
both the categorical model response and the resulting executable decision are
recorded in the audit. The derived dataset is:

```text
sft/output/privileged_teacher_narrative_directional_v7
```

This migration reuses all accepted teacher labels and therefore requires no
additional Luna campaign. Train and run the held-out and three-case smoke gates
with:

```bash
./run_directional_narrative_sft.sh
```

## 10. Reproduction and verification

Run the narrative and SFT unit tests with:

```bash
cd /home/lpagg/projects/moos-llm
.venv/bin/python -m unittest \
  tests.test_render_bridge_narratives \
  tests.test_agent_oow \
  tests.test_prepare_privileged_teacher_sft \
  tests.test_prepare_prompt_matched_sft
```

Run the paid-campaign preflight without creating files or API calls:

```bash
./run_luna_500m_generation.sh --check
```

The narrative renderer is deterministic: identical compact states, history,
threshold environment, and stale-age settings produce identical narrative text.

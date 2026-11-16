# Random training encounters

The OpenAI teacher campaign uses the `curriculum` profile. Each scenario contains
between one and three randomized targets. Every target is assigned a textbook
encounter: head-on, crossing from starboard, crossing from port, own ship
overtaking, or the target overtaking own ship. Orientation, approach timing,
target speed, and CPA offset vary by seed. These scenarios are independent of
the fixed Imazu evaluation suite and are intended to provide distinct
fine-tuning demonstrations.

The curriculum samples 50% single-contact canonical encounters, 25% perturbed
single-contact encounters, 15% controlled two- or three-contact encounters, and
10% two-stage recovery encounters. CPA offsets span critical (50–200 m), caution
(200–500 m), and safe (500–800 m) bands. Multi-contact scenarios contain one
deliberately controlling contact rather than synchronizing a dense fleet onto
the same conflict point.

The original dense generator remains available as the `stress` profile. It may
contain up to 50 contacts and is not the default teacher-data campaign.

The `targeted` profile alternates between two contacts crossing the same future
conflict point from opposite sides, where stopping may be defensible, and a
two-contact overtaking geometry with both contacts to starboard, where a port
alteration away from both may be defensible. These are candidate geometries,
not forced labels: the teacher
still selects the action from the observed situation. Targeted campaigns write
accepted stop and negative-degree `alter_course` examples separately to
`sft_rare_stop_port.jsonl` so routine monitoring snapshots do not worsen the
class imbalance.

`tools/generate_random_scenario.py` creates seeded Imazu-style fleet files.
Each fleet keeps own ship configured as a 12-knot power-driven vessel while
varying contact count, positions, goals, vessel types, speeds, CPA band, and
encounter templates. The generator deliberately
includes safe/distant and diverging traffic, port and starboard crossings,
both overtaking directions, head-on, parallel, and stationary contacts.
Encounter kinds are sampled independently, so multi-contact cases may contain
repeated kinds. Generated files live under `generated/` and are not committed;
campaign logs retain their seed and complete scenario metadata.

Own ship receives five successive objectives and the scenario completes after
it reaches the fifth. Each contact receives a longer queue of 25 objectives, so
traffic is continuously retasked instead of stopping at its initial destination
while own ship is still underway. Initial positions remain uniformly distributed
by area throughout the disk. Non-stationary objectives lie in the outer 82–97%
radial annulus, within 60 degrees of the direction opposite the previous point.
This produces varied chords through the operating area without placing every
vessel position exactly on a circle.

For large campaigns, up to the first twelve contacts are guaranteed near-term
crossing or head-on encounters while at least half remain drawn from the full
random mixture. Their starts are synchronized so they reach staggered points
along the middle 68% of own ship's initial route at approximately the same time
as own ship. The first encounter is at least 1,100 metres down-route, leaving
startup time before traffic enters the 1,500-metre decision radius. Remaining
contacts retain the full random mixture. This spreads actionable traffic over
the voyage while preserving background, safe, and later-developing encounters.

## What is randomized

All distances are in metres, headings in degrees, and speeds in knots. Values
come from uniform distributions unless a weight is shown. A seed reproduces the
same scenario; campaign seeds start at `--seed` and increase by one per run.

| Variable | Minimum | Maximum | Notes |
|---|---:|---:|---|
| Contacts per scenario | 1 | 50 | Campaign default is 1–50; `--contacts N` fixes the count for a single generated scenario. |
| Own-ship heading | 0 | 360 | Full-circle course. |
| Own-ship speed | 12 | 12 | Fixed campaign configuration. |
| Scenario centre X / Y | -80 | 80 | Each axis is sampled independently. |
| Own-ship route length | 900 | 1,300 | Start and goal are half this distance either side of the centre. |
| Encounter position along own route | -220 | 300 | Applied before the lateral CPA offset. |
| Normal CPA offset | 15 | 1,000 | Randomly placed to port or starboard; see CPA bands below. |
| Initial generic contact speed | 7 | 20 | Some encounter types replace this with a type-specific range. |

| CPA band | Absolute lateral offset | Weight |
|---|---:|---:|
| `critical` | 15–50 | 8% |
| `high` | 50–100 | 12% |
| `medium` | 100–250 | 25% |
| `low` | 250–500 | 30% |
| `clear` | 500–1,000 | 25% |

| Encounter kind | Weight | Course relative to own ship | Contact speed | Geometry notes |
|---|---:|---:|---:|---|
| `distant` | 18% | -35° to +35° | 5–20 | Lateral offset 1,100–1,900; marked `irrelevant`. |
| `diverging` | 12% | ±25° to ±100° | 8–20 | Starts 40–180 beyond the encounter point and opens away; marked `opening`. |
| `parallel` | 12% | -3° to +3° | own speed ±1.5, clamped to 4–20 | Lateral offset 180–900; marked `safe_parallel`. |
| `crossing` | 20% | ±50° to ±125° | 7–20 | Port/starboard chosen from the CPA-offset side. |
| `head_on` | 12% | 170°–190° | 7–20 | Starts 430–700 before and ends 500–800 after the encounter point. |
| `we_are_overtaking_contact` | 9% | -7° to +7° | 45–80% of own speed, floor 4 | Contact begins 140–360 behind its track centre. |
| `contact_overtaking_us` | 9% | -7° to +7° | own speed +2 to 19.5, capped at 20 | Contact begins 620–780 behind its track centre. |
| `stationary` | 8% | +110° to +250° | 6–13 | Very short 20–70 route ending at the encounter point. |

Kinds are selected with replacement, so the weights are probabilities per
contact rather than quotas per scenario. Coordinates and metadata are rounded
to three decimal places in the generated files.

## Campaign options

| Option | Default | Allowed values / effect |
|---|---:|---|
| `--runs N` | 10 | Positive integer; number of scenarios. |
| `--seed N` | 1 | Non-negative integer; first run uses N, then N+1, and so on. |
| `--min-contacts N` | 1 | Integer from 1 to `--max-contacts`. |
| `--max-contacts N` | 24 | Integer from `--min-contacts` to 24. |
| `--timeout N` | 1,200 | Positive integer seconds per scenario. |
| `--rag` / `--no-rag` | `--no-rag` | Enable or disable MAIB RAG. |
| `--oow base\|finetuned` | `base` | Select the base Qwen OOW or the fine-tuned adapter; both receive the same deterministic transform and prompt. |

Generate and run one scenario:

```bash
.venv/bin/python tools/generate_random_scenario.py \
  --seed 42 --contacts 4 \
  --output mission/scenarios/random/generated/manual/seed_42.ini

./run_simulator.sh --deploy --nogui --no-rag \
  --scenario mission/scenarios/random/generated/manual/seed_42.ini \
  --tiff mission/scenarios/imazu/ocean_background_4096.tif
```

Run an unattended campaign:

```bash
./run_random_campaign.sh --runs 100 --seed 1000 --min-contacts 1 --max-contacts 24
```

Run randomized classic teacher encounters through the OpenAI API:

```bash
./run_teacher_campaign.sh
TEACHER_CAMPAIGN_RUNS=100 ./run_teacher_campaign.sh
```

Run the rare-action candidate campaign:

```bash
./run_teacher_targeted_campaign.sh
TEACHER_TARGETED_RUNS=100 ./run_teacher_targeted_campaign.sh
```

Combine completed campaigns into a filtered student dataset. This excludes
collisions, timeouts, unfinished missions, sub-threshold runs, and simulator
`skip` decisions:

```bash
.venv/bin/python tools/build_teacher_dataset.py \
  logs/campaigns/OLD_CAMPAIGN logs/campaigns/NEW_CAMPAIGN \
  --minimum-range-m 50 --output sft/teacher_combined.jsonl
```

Run matched base and fine-tuned OOW campaigns with the same seeds. Both modes
use `agents/skills/oow_decision.md` and the same deterministic bridge narrative;
only the requested vLLM model changes:

```bash
./run_random_campaign.sh --runs 100 --seed 1000 --oow base --no-rag
./run_random_campaign.sh --runs 100 --seed 1000 --oow finetuned --no-rag
```

Every run gets a separate audit log under `logs/campaigns/`. Results distinguish
completion, collision, timeout, and software failure. A per-run timeout prevents
a paused scenario from blocking the rest of the campaign.

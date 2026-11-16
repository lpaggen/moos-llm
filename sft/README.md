# OOW supervised fine-tuning

This directory now exposes the retained directional-narrative v7 workflow.
Earlier v1–v6 experiments, invalid prompt-conversion datasets, and DPO-only
artifacts are preserved in the reversible repository cleanup archive rather
than mixed with the active training surface.

## Retained lineage

```text
10 accepted teacher-campaign directories
  -> output/teacher_campaigns_luna_expanded_v5
  -> output/privileged_teacher_base_narrative_v6_clean
  -> output/privileged_teacher_narrative_ablation_v6_clean
  -> output/privileged_teacher_narrative_directional_v7
  -> ../models/qwen3_8b_oow_privileged_luna_narrative_directional_v7
```

The final transformation changes signed course alterations into an explicit
`port`/`starboard` direction plus unsigned magnitude. It does not regenerate or
silently relabel teacher decisions.

The v7 manifest records:

| Split | Rows | Missions |
|---|---:|---:|
| Natural training | 5,865 | 262 |
| Balanced training view | 2,068 | 261 |
| Validation | 724 | 34 |
| Test | 754 | 34 |

There is zero mission overlap between train, validation, and test. The source
acceptance threshold is 500 m. The final action-quality gate rejected 208 rows:
165 passive actions during acute projected sub-500 m risk, 33 port actions
without candidate lookahead, six port actions without an explicit starboard
exception, three selected actions failing the 500 m lookahead, and one unsafe
route rejoin.

Authoritative metadata and hashes are in
`output/privileged_teacher_narrative_directional_v7/manifest.json`.

## Current files

- `prepare_teacher_campaigns.py`: quality-filter and mission-split teacher runs.
- `prepare_privileged_teacher_sft.py`: serialize the student-facing narrative
  prompt while retaining privileged teacher labels.
- `filter_action_quality_sft.py`: enforce the 500 m and directional gates.
- `prepare_narrative_ablation_sft.py`: construct the short-prompt narrative
  ablation.
- `convert_directional_sft.py`: produce the explicit directional action schema.
- `preflight_directional_sft.py`: fail-closed v7 validation.
- `train_qwen3_oow.py`: QLoRA trainer.
- `evaluate_qwen3_oow.py`: held-out generation evaluator.
- `prepare_captain_critic_sft.py`: Captain-critic batch/SFT preparation.

## Validate the retained dataset

```bash
.venv/bin/python -m sft.preflight_directional_sft \
  sft/output/privileged_teacher_narrative_directional_v7 \
  --minimum-source-clearance-m 500
```

## Train a fresh v7 adapter

The checked-in/local v7 output paths already exist, so the audited launcher
correctly refuses to overwrite them. To retrain, point all outputs at new paths:

```bash
DIRECTIONAL_SFT_MODEL_OUTPUT=models/qwen3_8b_oow_directional_v7_retrain \
DIRECTIONAL_SFT_HELDOUT_OUTPUT=sft/output/directional_v7_retrain_heldout \
DIRECTIONAL_SFT_SMOKE_OUTPUT=logs/evaluations/imazu/directional_v7_retrain_smoke \
./run_directional_narrative_sft.sh
```

The launcher runs directional preflight, one-epoch rank-16 QLoRA training,
held-out generation, metric gates, and Imazu cases 13, 16, and 22 as a smoke
gate.

## Existing full evaluation

The retained 22-case v7 evaluation is:

```text
logs/evaluations/imazu/privileged_luna_narrative_directional_v7_full_warp5
```

The lockstep comparison is:

```text
logs/evaluations/imazu/privileged_luna_narrative_directional_v7_lockstep_30s_warp20
```

## Captain-critic teacher data

Prepared batch inputs and job metadata are retained in
`output/captain_critic_teacher_v1/`. They are separate from the OOW v7 dataset
and must not be mixed into OOW training without running the Captain-specific
validation in `prepare_captain_critic_sft.py`.

## Historical material

The cleanup archive is documented at
`../temp/repository_cleanup_20261116/README.md`. Its payload keeps original
paths for restoration, including old SFT datasets, DPO inputs/configuration,
adapters, and launchers.

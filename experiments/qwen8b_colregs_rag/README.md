# Vanilla Qwen3-8B OOW with COLREG RAG

An isolated baseline for testing an **unmodified `Qwen/Qwen3-8B`** as the
Officer of the Watch. The model receives the observable bridge narrative plus
retrieved verbatim COLREG provisions. No LoRA adapter or SFT checkpoint is
loaded.

Before retrieval, the bridge narrative is reduced to a compact observation-only
query for each contact (own-ship status/type, visibility, contact geometry,
CPA/TCPA, bearing trend, persisted encounter state, and current maneuver).
Persisted encounter state comes from the deterministic runtime state and is
kept outside the model-visible narrative to avoid SFT target leakage. The pipeline retrieves each
encounter independently and merges duplicate rules, so one contact cannot hide
another contact's applicable rule. Retrieval traces expose these under
`queries`; the legacy singular `query` remains populated for one-contact cases.

The retrieval pipeline then has two explicit stages:

1. deterministic BM25 candidate retrieval from `agents/colregs/colregs.json`;
2. an optional reranker implementing the small `Reranker` protocol.

Every decision can be logged with the complete candidate pool, first-stage
scores, reranker scores, and final selection. Those logs are suitable for later
conversion into pointwise or pairwise neural-reranker examples.

## Quick start

The OpenAI-compatible endpoint must serve the base model without an adapter:

```bash
vllm serve Qwen/Qwen3-8B --port 8001 --max-model-len 8192
```

Run retrieval only:

```bash
.venv/bin/python -m experiments.qwen8b_colregs_rag.cli retrieve \
  --query "power-driven crossing contact on starboard side collision risk"
```

Make a decision from an existing OOW narrative:

```bash
.venv/bin/python -m experiments.qwen8b_colregs_rag.cli decide \
  --input sft/data/in_narratives.jsonl --id state::c673fb8e34923403 \
  --cruise-speed 17 \
  --log experiments/qwen8b_colregs_rag/out/runs.jsonl
```

The model-facing speed field is qualitative: `slow`, `normal`, or `fast` for
`set_speed`, and `none` for other actions. The runtime resolves `fast` to the
configured maximum available to this baseline, `normal` to the configured
recovery/cruise speed, and `slow` to 70% of cruise (the Imazu 12 kn / 8.4 kn
ratio, with a small minimum-way floor). In Imazu 16 the configured maximum and
normal cruise are identical, so `fast` and `normal` resolve to the same command.
The model never chooses a numeric propulsion or helm command. It selects
`slow`/`normal`/`fast` for speed and `port`/`starboard` for a course alteration;
the runtime resolves those intentions to configured numeric commands.

The default reranker is the pass-through `identity` implementation. A future neural reranker can implement
`Reranker.rerank(query, candidates)` without changing the OOW or retriever.

Once traces and trusted canonical rule judgments exist, export pointwise
reranker examples with:

```bash
.venv/bin/python -m experiments.qwen8b_colregs_rag.export_reranker_data \
  --traces experiments/qwen8b_colregs_rag/out/runs.jsonl \
  --judgments sft/output/colregs_labeled/labeled.jsonl \
  --output experiments/qwen8b_colregs_rag/out/reranker_train.jsonl
```

Keep scenario IDs grouped when splitting this file to prevent temporal leakage
between nearby snapshots of the same encounter.

## Important experimental boundary

Retrieved rules are legal evidence, not hidden simulator labels. Encounter
type, own-ship role, and the maneuver remain the OOW model's responsibility.
For evaluation, compare decisions against a separately maintained gold set;
never inject the target label into the retrieval query.

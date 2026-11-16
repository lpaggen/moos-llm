# Prompt ablation and decision timing

`analyze_prompt_ablation.py` compares one fine-tuned Imazu campaign with one or
more baseline campaigns. It reports prompt size, time between consecutive model
decisions, prompt-to-decision latency, logged OOW inference duration, and prompt
reduction percentages.

Run from the repository root:

```sh
.venv/bin/python tools/analyze_prompt_ablation.py \
  --finetuned logs/evaluations/imazu/20261015T092200Z_finetuned \
  --baseline base=logs/evaluations/imazu/20261014T151153Z_base \
  --baseline colregs-rag=logs/evaluations/imazu/20261014T215732Z_colregs-rag \
  --output logs/evaluations/imazu/20261015T092200Z_finetuned/analysis/prompt_ablation.json
```

Prompt size includes the system and user messages sent to the OOW. For RAG
runs, the script reconstructs the added retrieval wrapper and operational rule
summaries from each cycle's logged `oow_colregs_retrieval` event. Invalid-output
retry messages are not logged and therefore cannot be included.

Character and UTF-8 byte counts are exact. The token count is an explicitly
labelled estimate of `ceil(characters / 4)`, not a tokenizer-exact measurement.
The percentage reduction is

```text
100 * (baseline prompt size - fine-tuned prompt size) / baseline prompt size
```

A negative reduction denotes prompt growth. `inter_decision_seconds` measures
the wall-clock interval between consecutive `oow_decision` events within each
case. `prompt_to_decision_seconds` measures model-response latency from the
corresponding `oow_prompt_input`; `logged_oow_duration_seconds` is the runtime's
monotonic-clock measurement of the OOW call.


# COLREG decision-tree experiment

This experiment replaces COLREG RAG selection with the repository's deterministic
[`classify_colregs`](../../agents/colregs/classifier.py) function:

```text
bridge facts → deterministic applicability classifier → rule numbers
            → exact lookup of existing simplified summaries → Qwen OOW decision
```

There is no BM25 search, embedding, reranking, top-k truncation, or LLM rule
selector. All selected rule numbers receive their existing summaries from
[`colregs_ship_guidance.json`](../qwen8b_colregs_rag/colregs_ship_guidance.json).
The classifier's extra maneuver recommendations and role labels are logged but
are not added to the model prompt. The model still chooses the action.

This uses the repository's COLREG applicability tree; it has not been verified
as a reproduction of any particular published IMO decision tree.

The tree uses bridge encounter/risk labels, vessel categories, bearings, contact
ages, and configured visibility/channel/TSS assumptions. Classification runs
after encounter persistence and before those labels are removed from the model's
ordinary observation input. It selects standing Rules 2/5/6/7, Rule 11 for visible
contacts, and encounter rules such as 13, 14, 15/16 or 15/17. Unknown facts and
stale contacts are recorded. Visibility currently defaults to 10,000 m; clear
weather does not automatically make contacts beyond that range visible.

Validate all scenarios without starting services:

```sh
./run_imazu_evaluation.sh --architecture colregs-tree --cases 1-22 --check
```

Run the experiment (starts simulator and model services):

```sh
./run_imazu_evaluation.sh --architecture colregs-tree --cases 1-22 --gui
```

Use `colregs-tree-reasoning` for the thinking variant. For a single scenario:

```sh
./run_simulator.sh --scenario imazu/01 --no-rag --colregs-tree --oow base --deploy
```

Compare against `colregs-rag` using the same scenarios, model, time warp, cycle
limit and timeout. Both profiles use the full base navigation prompt, temperature
zero, the same simplified-summary source, and the same qualitative course/speed
schema and deterministic command conversion. Tree selection has no six-rule cap,
so rule count and prompt length may differ. This measures the effect of the
selection pipeline, including its deterministic encounter/risk inputs.

Audit records retain the existing `oow_colregs_retrieval` event name for tool
compatibility; `selector: deterministic_colregs_tree` identifies this experiment.
They include selected numbers/summaries, per-contact classification, missing
facts, assumptions, skipped contacts, and resolved model action intents.
Startup logs distinguish tree selection from RAG. Both modes cannot be enabled
together. The experiment is opt-in; existing architecture defaults are unchanged.

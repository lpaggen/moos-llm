# GPT test of MAIB chunk applicability to recent audit states

This experiment asks a deliberately narrow question: for a specific bridge state
recorded in a completed evaluation audit, can one retrieved MAIB passage be used
meaningfully as advisory evidence? If the answer is yes, GPT must extract one
lesson card from that passage alone. If the answer is no, no card is permitted.

The defaults use five real cycles from the latest completed **evidence-bearing**
MAIB run:

- `case_01:44`: head-on, risk developing, one contact;
- `case_02:47`: crossing, risk developing, one contact;
- `case_13:55`: crossing, monitoring, three contacts;
- `case_16:81`: crossing, action required, three contacts;
- `case_20:74`: overtaking, risk developing, three contacts.

Each cycle has two retrieved chunks, producing ten independent API judgments.
The newer `maib_critic_no_evidence_lockstep_30s_warp20` condition cannot supply
source passages by design, so it is not the source run for this test.

## 1. Inspect the prepared requests without calling the API

From the repository root:

```bash
.venv/bin/python tools/test_maib_chunk_applicability.py --dry-run
```

The command creates a timestamped directory below
`reports/maib_chunk_applicability/`. Inspect `prepared_requests.jsonl` to see the
exact narrative, structured state, OOW proposal, retrieval context, source chunk,
instructions, and output schema that would be sent. No API key is needed.

To inspect only the first state–chunk pair:

```bash
.venv/bin/python tools/test_maib_chunk_applicability.py --dry-run --max-pairs 1
```

## 2. Run the ten direct API judgments

Set the API key in the same shell, then run:

```bash
export OPENAI_API_KEY='your-real-key'
.venv/bin/python tools/test_maib_chunk_applicability.py --model gpt-6.1-sol
```

To test one audit cycle only:

```bash
.venv/bin/python tools/test_maib_chunk_applicability.py \
  --model gpt-6.1-sol \
  --sample case_01:44
```

Do not reuse `--output-dir`: the script refuses to overwrite an existing result
directory. It does not modify the audit logs, `accidents/index.jsonl`, or the
existing extracted cards.

## 3. Read the outputs

Every run stores:

- `prepared_requests.jsonl`: complete prompts and provenance;
- `cost_estimate.json`: request count and approximate input cost;
- `results.jsonl`: validated pair-level judgments and any extracted cards;
- `attempts.jsonl`: every raw API response and its validation outcome;
- `failures.jsonl`: API, schema, or grounding failures;
- `summary.json`: aggregate counts;
- `REPORT.md`: concise human-readable result table.

The prior captain's evidence assessment is saved for post-hoc comparison but is
not included in the GPT prompt. Local validation requires exact narrative quotes,
exact source quotes, the supplied source page, and a null card whenever GPT says
the chunk is not meaningfully applicable.

## Repair only failed pairs

If a run contains local-validation or API failures, preserve its successful
results and retry only the failures:

```bash
.venv/bin/python tools/test_maib_chunk_applicability.py \
  --model gpt-6.1-sol \
  --retry-run reports/maib_chunk_applicability/20261115T130436Z
```

This creates a new result directory containing the previous valid results plus
the repaired judgments. It does not call the API again for successful pairs and
does not overwrite the original run.

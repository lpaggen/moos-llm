# Imazu navigation-state lesson-card retrieval benchmark

This offline benchmark uses the final 503-card retained corpus and one archived retrieval-eligible state from each of the 22 Imazu scenarios. The deterministic state-selection rule is the first `numeric_observations_only` MAIB retrieval in each case of the frozen warp-5 lesson-card campaign. Each selected query and its hidden encounter preferences were reconstructed from the archived bridge snapshot and OOW decision and required to equal the logged runtime values exactly.

Four top-two configurations are compared with unchanged retrieval parameters:

1. BM25 (`k1=1.5`, `b=0.75`, evidence weighting, minimum score 5.0);
2. BGE dense retrieval (`BAAI/bge-small-en-v1.5`, existing query prefix, normalised vectors, evidence weighting);
3. BM25+BGE reciprocal-rank fusion (top-50 channel pools, `1/(60+r)`, evidence weighting);
4. the same RRF ranking with the existing categorical encounter-type and phase preferences.

All four apply the runtime source-diversity rule of at most one card per MAIB report and return two cards. No reranker is enabled. The union contains 137 unique state–card pairs (maximum 176).

`relevance_labels.csv` is the only file intended for the blinded annotator. `private_pool_key.jsonl` and `rankings.jsonl` reveal methods and must remain hidden until labels are final. No language model is used for relevance grading. Once all rows contain 0, 1, or 2, the `score` command writes `summary.json`, `per_scenario.jsonl`, and `RESULTS.md`.

The benchmark reports Precision@2, Hit@2, and nDCG@2. Only label 2 counts as relevant for Precision and Hit. nDCG uses graded gains `2^label - 1`. Because judgments cover the pooled top-two candidates rather than all 503 cards, the benchmark does not report corpus-wide recall.

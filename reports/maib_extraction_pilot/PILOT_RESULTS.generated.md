# Final MAIB evidence-page retrieval benchmark

This file is generated from the frozen curated card corpus. The thesis-ready narrative,
including extraction and semantic-curation methodology, is in `REPORT.md`.

## Frozen inputs

- indexed passages: 2373
- retained cards / benchmark queries: 503
- represented source-report clusters: 101
- verified quotations in exported reasoning records: 1008 / 1008
- card artifact: `accidents/cards_decision_style_curated/cards.retained.jsonl`
- card SHA-256: `2523ef4b019dce48bdf9b0826f6bc73dd289a8646770383aed0204b1a715a292`
- query mode: `generic`
- RRF candidate depth passed to the cross-encoder: 20

## Task

For each retained card, rank the indexed chunks from its known source report using a
generic schema-derived evidence query. Card-specific findings, warning signs, lessons,
and quotations are not inserted into the query. The card's cited pages are weak,
machine-derived targets rather than independent human relevance labels. Chunk rankings
are collapsed to unique page rankings before scoring.

## Results

| Method | Hit@1 | Hit@3 | Hit@5 | Reference-page recall@5 | MRR | nDCG@5 |
|---|---:|---:|---:|---:|---:|---:|
| BM25 | 0.139 | 0.423 | 0.652 | 0.561 | 0.349 | 0.355 |
| BGE dense | 0.113 | 0.358 | 0.565 | 0.489 | 0.314 | 0.308 |
| BM25 + BGE RRF | 0.125 | 0.429 | 0.592 | 0.522 | 0.336 | 0.336 |
| RRF + cross-encoder | 0.109 | 0.406 | 0.604 | 0.517 | 0.324 | 0.322 |

BM25 has the highest Hit@1, Hit@5, reference-page recall@5, MRR, and nDCG@5. RRF has
the highest Hit@3. Relative to RRF, cross-encoder reranking changed:

- Hit@5 by +0.012, with report-cluster bootstrap
  95% interval [-0.055,
  +0.075];
- MRR by -0.012, with interval
  [-0.041,
  +0.017]; and
- nDCG@5 by -0.014, with interval
  [-0.051,
  +0.024].

All intervals contain zero. This run therefore does not demonstrate a reliable
cross-encoder improvement over RRF. The cross-encoder scored
9082 query-passage pairs in
236.6 seconds on CPU. Dense query encoding required
4.7 seconds.

## Scope limitation

This benchmark measures within-report recovery of pages cited by generated cards. It
does not evaluate whole-corpus report discovery, live-state lesson-card retrieval,
semantic extraction accuracy, or navigation outcomes. The cited pages are not a human
gold standard.

## Corpus inventory

- readable reports: 107
- PDF pages: 1761
- pages with at least 80 extracted characters: 1724
- pages containing embedded image objects: 640
- visual-only candidates excluded from textual interpretation: 31

## Provenance

```json
{
  "index": "accidents/index.jsonl",
  "index_sha256": "3f04d0d7a2bf8ccca705ac0bf3cb66f9925f4f95d12fbf0c47247d1012575caa",
  "embeddings": "accidents/embeddings.npy",
  "embeddings_sha256": "aa09a89eeda61f306ac6f6d2342f50ad4496e21c99a27975a4ebc492602d94ec",
  "cards_path": "accidents/cards_decision_style_curated/cards.retained.jsonl",
  "cards_sha256": "2523ef4b019dce48bdf9b0826f6bc73dd289a8646770383aed0204b1a715a292",
  "embedding_model": "BAAI/bge-small-en-v1.5",
  "reranker_model": "cross-encoder/ms-marco-MiniLM-L6-v2",
  "candidate_count": 20,
  "query_mode": "generic",
  "documents": 2373,
  "card_records": 503,
  "timings": {
    "dense_query_encoding_seconds": 4.723679000991979,
    "cross_encoder_seconds": 236.6335591990064,
    "cross_encoder_pairs": 9082
  }
}
```

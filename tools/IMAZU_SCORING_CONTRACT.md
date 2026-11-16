# Imazu scoring contract: `imazu-frozen-v1`

Frozen on 2026-11-03. Results reported under this contract must not alter the
weights, gates, thresholds, reference labels, aggregation, or missing-data
policy. A changed definition requires a new contract version and complete
rescoring of every compared campaign.

## Composite

| Axis | Weight |
|---|---:|
| Safety | 0.35 |
| Physical COLREG compliance | 0.10 |
| COLREG citation agreement | 0.10 |
| Temporal efficiency | 0.10 |
| Spatial efficiency | 0.10 |
| Manoeuvre economy | 0.10 |
| Smoothness | 0.15 |

The weights sum to 1.00. The citation axis and physical-compliance axis split
the previous 0.20 compliance allocation; all other weights remain unchanged.
A collision sets the composite to zero. A non-arrival is capped at 0.20 under
the existing retrospective-scoring policy.

## Citation agreement

For every cycle containing an `oow_colregs_oracle` event:

1. Select the Eriksen oracle's primary contact.
2. Union its complete `applicable_rules` set with persistent Rules 2, 5, 6,
   and 7. These four rules remain in the reference even when no contact is
   present; encounter-specific rules are added when applicable.
3. Extract only numbers in explicit `Rule ...`, `Rules ...`, or `COLREG ...`
   citation clauses from the decision reason.
4. Compute set precision, recall, and F1 for that cycle.
5. The case citation score is macro F1 across all oracle cycles.

For ordinary OOW runs, citations are read from `oow_decision`. For the
Captain-critic architecture, citations are read from the final
`captain_decision`. Empty prediction and empty reference score 1. A missing
decision receives an empty prediction (and therefore scores zero because the
persistent reference is non-empty). A case with no oracle cycles receives a
citation score of zero (fail closed).

The standalone citation audit may additionally report micro F1, exact match,
presence, and common missing/unsupported rules. Those diagnostics do not alter
the composite.

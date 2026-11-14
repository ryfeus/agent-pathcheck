---
anchor: "rule:costs.caveats_required"
kind: "rule"
level: "tested"
owner: "reference-example"
---
# costs.caveats_required

<!-- generated:start -->
Carry exactly the required caveats

Level: tested

Owner: reference-example

Source: fixture design

Required caveats must survive into the checked answer.

References: [CostComparisonWithCaveats](../entities/cost_comparison_with_caveats.md)
<!-- generated:end -->

## Meaning

Service churn requires an explicit caveat in the checked comparison.

## Notes (advice, not checked)

New or removed services may reflect migrations or one-off changes rather than usage.

## Rationale

Carry this limitation with the answer; see [required caveats](costs.caveats_required.md).

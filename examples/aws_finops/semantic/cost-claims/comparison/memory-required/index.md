---
anchor: "examples.aws_finops.policy:memory-required:index"
kind: "index"
---
# Semantic map

<!-- generated:start -->
Policy: memory-required
Documentation guides planning; it is never evidence.

This question requires: CostDelta({"after":{"source":{"account":"production","group_by":"SERVICE","metric":"NetAmortizedCost","month":"2026-10"}},"before":{"source":{"account":"production","group_by":"SERVICE","metric":"NetAmortizedCost","month":"2026-09"}}}); ServiceChanges({"after":{"account":"production","group_by":"SERVICE","metric":"NetAmortizedCost","month":"2026-10"},"before":{"account":"production","group_by":"SERVICE","metric":"NetAmortizedCost","month":"2026-09"},"top":null})

Entities: [abstain](entities/abstain.md), [answer](entities/answer.md), [cost_delta](entities/cost_delta.md), [cost_total](entities/cost_total.md), [service_change](entities/service_change.md), [service_changes](entities/service_changes.md), [service_cost](entities/service_cost.md)

Measures: [cost_query](measures/cost_query.md)

Relationships: [cost_scope_account](relationships/cost_scope_account.md), [cost_scope_metric](relationships/cost_scope_metric.md), [cost_scope_month](relationships/cost_scope_month.md), [service_cost_service](relationships/service_cost_service.md)

[Rules](rules/index.md) · [Vocabulary](glossary.md)
<!-- generated:end -->

## Meaning

This page describes the active host contract for scoped answer claims. Only completed query evidence can support their computed values.

## Notes (advice, not checked)

Documentation explains the policy; reading it does not complete an evidence request.

## Rationale

See the [required-claim rule](rules/claims.required_missing.md) for the checked boundary.

---
anchor: "examples.aws_finops.policy:CostQuery"
kind: "measure"
---
# CostQuery

<!-- generated:start -->
Field `account`: `Literal['production']`; required; constraints `[]`.
Field `month`: `Literal['2026-09', '2026-10']`; required; constraints `[]`.
Field `metric`: `Literal['NetAmortizedCost']`; required; constraints `[]`.
Field `group_by`: `Literal['SERVICE']`; required; constraints `[]`.

```python
class CostQuery(PolicyModel):
    account: Literal['production']
    month: Literal['2026-09', '2026-10']
    metric: Literal['NetAmortizedCost']
    group_by: Literal['SERVICE']
```

Returns [ServiceCost](../entities/service_cost.md) as a validated batch.

Relationships: [cost_scope_account](../relationships/cost_scope_account.md), [cost_scope_month](../relationships/cost_scope_month.md), [cost_scope_metric](../relationships/cost_scope_metric.md)
<!-- generated:end -->

## Meaning

This page describes the active host contract for scoped answer claims. Only completed query evidence can support their computed values.

## Notes (advice, not checked)

Documentation explains the policy; reading it does not complete an evidence request.

## Rationale

See the [required-claim rule](../rules/claims.required_missing.md) for the checked boundary.

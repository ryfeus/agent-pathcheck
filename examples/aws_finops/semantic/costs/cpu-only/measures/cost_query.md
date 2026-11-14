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

Read the configured amortized accounting basis for the requested period. The fixture also contains unblended-cost decoys; those values are not interchangeable.

## Notes (advice, not checked)

Amounts use integer USD cents. This synthetic reference does not establish live pricing or operational suitability.

## Rationale

Completed observations ground the claims described here; documentation supplies meaning rather than evidence. See [costs.total](../rules/costs.total.md).

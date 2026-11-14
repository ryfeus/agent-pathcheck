---
anchor: "examples.aws_finops.policy:ServiceCost"
kind: "entity"
---
# ServiceCost

<!-- generated:start -->
Field `service`: `str`; required; constraints `[]`.
Field `cost_cents`: `int`; required; constraints `[]`.

```python
class ServiceCost(PolicyModel):
    service: str
    cost_cents: int
```

Returned by: [CostQuery](../measures/cost_query.md)

Relationships: [service_cost_service](../relationships/service_cost_service.md)
<!-- generated:end -->

## Meaning

A service aggregate inherits account, period, and accounting basis from its completed CostQuery batch.

## Notes (advice, not checked)

Amounts use integer USD cents. This synthetic reference does not establish live pricing or operational suitability.

## Rationale

Completed observations ground the claims described here; documentation supplies meaning rather than evidence. See [costs.total](../rules/costs.total.md).

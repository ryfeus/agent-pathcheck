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

This page describes the active host contract for scoped answer claims. Only completed query evidence can support their computed values.

## Notes (advice, not checked)

Documentation explains the policy; reading it does not complete an evidence request.

## Rationale

See the [required-claim rule](../rules/claims.required_missing.md) for the checked boundary.

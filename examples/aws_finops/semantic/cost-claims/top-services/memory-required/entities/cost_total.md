---
anchor: "examples.aws_finops.policy:CostTotal"
kind: "entity"
---
# CostTotal

<!-- generated:start -->
Field `source`: `CostQuery`; required; constraints `[]`.
Field `value`: `int | None`; optional; constraints `[]`.

```python
class CostTotal(Claim):
    source: CostQuery
    value: int | None = None

    @model_validator(mode='before')
    @classmethod
    def check(cls, data):
        data = claim_input(data)
        if not isinstance(data, dict) or 'source' not in data:
            return data
        source = CostQuery.model_validate(data['source'])
        actual = sum(row.cost_cents for row in current_evidence().require(source))
        return settle(data, actual, 'claims.cost_total')
```

Rules: [claims.cost_total](../rules/claims.cost_total.md)
<!-- generated:end -->

## Meaning

This page describes the active host contract for scoped answer claims. Only completed query evidence can support their computed values.

## Notes (advice, not checked)

Documentation explains the policy; reading it does not complete an evidence request.

## Rationale

See the [required-claim rule](../rules/claims.required_missing.md) for the checked boundary.

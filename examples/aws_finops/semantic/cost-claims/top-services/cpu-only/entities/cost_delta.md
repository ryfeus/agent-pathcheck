---
anchor: "examples.aws_finops.policy:CostDelta"
kind: "entity"
---
# CostDelta

<!-- generated:start -->
Field `before`: `CostTotal`; required; constraints `[]`.
Field `after`: `CostTotal`; required; constraints `[]`.
Field `value`: `int | None`; optional; constraints `[]`.

```python
class CostDelta(Claim):
    before: CostTotal
    after: CostTotal
    value: int | None = None

    @model_validator(mode='before')
    @classmethod
    def check(cls, data):
        data = claim_input(data)
        if not isinstance(data, dict) or not {'before', 'after'} <= data.keys():
            return data
        before = CostTotal.model_validate(data['before'])
        after = CostTotal.model_validate(data['after'])
        return settle(data, after.value - before.value, 'claims.cost_delta')
```

Rules: [claims.cost_delta](../rules/claims.cost_delta.md)
<!-- generated:end -->

## Meaning

This page describes the active host contract for scoped answer claims. Only completed query evidence can support their computed values.

## Notes (advice, not checked)

Documentation explains the policy; reading it does not complete an evidence request.

## Rationale

See the [required-claim rule](../rules/claims.required_missing.md) for the checked boundary.

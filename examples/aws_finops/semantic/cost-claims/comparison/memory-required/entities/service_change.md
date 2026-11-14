---
anchor: "examples.aws_finops.policy:ServiceChange"
kind: "entity"
---
# ServiceChange

<!-- generated:start -->
Field `service`: `str`; required; constraints `[]`.
Field `change_cents`: `int`; required; constraints `[]`.

```python
class ServiceChange(PolicyModel):
    service: str
    change_cents: int
```
<!-- generated:end -->

## Meaning

This page describes the active host contract for scoped answer claims. Only completed query evidence can support their computed values.

## Notes (advice, not checked)

Documentation explains the policy; reading it does not complete an evidence request.

## Rationale

See the [required-claim rule](../rules/claims.required_missing.md) for the checked boundary.

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

This page explains a code-declared part of the active policy and its connections.

## Notes (advice, not checked)

Amounts use integer USD cents. This synthetic reference does not establish live pricing or operational suitability.

## Rationale

Completed observations ground the claims described here; documentation supplies meaning rather than evidence. See [costs.total](../rules/costs.total.md).

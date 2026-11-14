---
anchor: "examples.aws_finops.policy:EvidenceQuery"
kind: "measure"
---
# EvidenceQuery

<!-- generated:start -->
Field `account`: `Literal['production']`; required; constraints `[]`.
Field `metric`: `Literal['cpu']`; required; constraints `[]`.

```python
class EvidenceQuery(PolicyModel):
    account: Literal['production']
    metric: Literal['cpu']
```

Returns [Evidence](../entities/evidence.md) as a validated batch.
<!-- generated:end -->

## Meaning

This page explains a code-declared part of the active policy and its connections.

## Notes (advice, not checked)

Coverage is collected days, not recency. The synthetic catalog does not establish workload performance or migration feasibility.

## Rationale

Completed observations ground the claims described here; documentation supplies meaning rather than evidence. See [rightsizing.instance_evidence](../rules/rightsizing.instance_evidence.md).

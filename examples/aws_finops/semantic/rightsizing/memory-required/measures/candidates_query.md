---
anchor: "examples.aws_finops.policy:CandidatesQuery"
kind: "measure"
---
# CandidatesQuery

<!-- generated:start -->
Field `account`: `Literal['production']`; required; constraints `[]`.

```python
class CandidatesQuery(PolicyModel):
    account: Literal['production']
```

Returns [Candidate](../entities/candidate.md) as a validated batch.
<!-- generated:end -->

## Meaning

This page explains a code-declared part of the active policy and its connections.

## Notes (advice, not checked)

Coverage is collected days, not recency. The synthetic catalog does not establish workload performance or migration feasibility.

## Rationale

Completed observations ground the claims described here; documentation supplies meaning rather than evidence. See [rightsizing.instance_evidence](../rules/rightsizing.instance_evidence.md).

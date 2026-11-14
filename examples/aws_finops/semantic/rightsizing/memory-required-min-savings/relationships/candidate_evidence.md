---
anchor: "examples.aws_finops.policy:relationship:candidate_evidence"
kind: "relationship"
---
# candidate_evidence

<!-- generated:start -->
[Candidate.instance_id](../entities/candidate.md) → [Evidence.instance_id](../entities/evidence.md)

Evidence belongs to the instance; each metric has its own observation.
<!-- generated:end -->

## Meaning

This page explains a code-declared part of the active policy and its connections.

## Notes (advice, not checked)

Coverage is collected days, not recency. The synthetic catalog does not establish workload performance or migration feasibility.

## Rationale

Completed observations ground the claims described here; documentation supplies meaning rather than evidence. See [rightsizing.instance_evidence](../rules/rightsizing.instance_evidence.md).

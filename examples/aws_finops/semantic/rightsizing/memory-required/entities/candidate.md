---
anchor: "examples.aws_finops.policy:Candidate"
kind: "entity"
---
# Candidate

<!-- generated:start -->
Field `recommendation_id`: `str`; required; constraints `[]`.
Field `instance_id`: `str`; required; constraints `[]`.
Field `instance_type`: `str`; required; constraints `[]`.
Field `architecture`: `str`; required; constraints `[]`.
Field `target_type`: `str`; required; constraints `[]`.
Field `target_architecture`: `str`; required; constraints `[]`.
Field `performance_risk`: `float`; required; constraints `[Ge(ge=0), Le(le=4), AllowInfNan(allow_inf_nan=False)]`.
Field `monthly_savings_cents`: `int`; required; constraints `[]`.

```python
class Candidate(PolicyModel):
    recommendation_id: str
    instance_id: str
    instance_type: str
    architecture: str
    target_type: str
    target_architecture: str
    performance_risk: float = Field(..., ge=0, le=4, allow_inf_nan=False)
    monthly_savings_cents: int
```

Returned by: [CandidatesQuery](../measures/candidates_query.md)

Relationships: [candidate_evidence](../relationships/candidate_evidence.md), [candidate_target](../relationships/candidate_target.md), [candidate_instance](../relationships/candidate_instance.md)
<!-- generated:end -->

## Meaning

A candidate joins the observed inventory, recommendation, and target catalog. Target architecture is checked against the current architecture.

## Notes (advice, not checked)

Coverage is collected days, not recency. The synthetic catalog does not establish workload performance or migration feasibility.

## Rationale

Completed observations ground the claims described here; documentation supplies meaning rather than evidence. See [rightsizing.instance_evidence](../rules/rightsizing.instance_evidence.md).

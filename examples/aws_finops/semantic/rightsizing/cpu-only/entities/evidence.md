---
anchor: "examples.aws_finops.policy:Evidence"
kind: "entity"
---
# Evidence

<!-- generated:start -->
Field `instance_id`: `str`; required; constraints `[]`.
Field `metric`: `Literal['cpu', 'memory']`; required; constraints `[]`.
Field `p95_percent`: `float`; required; constraints `[Ge(ge=0), Le(le=100), AllowInfNan(allow_inf_nan=False)]`.
Field `days`: `int`; required; constraints `[Ge(ge=0)]`.

```python
class Evidence(PolicyModel):
    instance_id: str
    metric: Literal['cpu', 'memory']
    p95_percent: float = Field(..., ge=0, le=100, allow_inf_nan=False)
    days: int = Field(..., ge=0)
```

Returned by: [EvidenceQuery](../measures/evidence_query.md)

Relationships: [candidate_evidence](../relationships/candidate_evidence.md)
<!-- generated:end -->

## Meaning

Collected utilization and coverage belong to an instance and metric. Missing memory collection does not establish low utilization.

## Notes (advice, not checked)

Coverage is collected days, not recency. The synthetic catalog does not establish workload performance or migration feasibility.

## Rationale

Completed observations ground the claims described here; documentation supplies meaning rather than evidence. See [rightsizing.instance_evidence](../rules/rightsizing.instance_evidence.md).

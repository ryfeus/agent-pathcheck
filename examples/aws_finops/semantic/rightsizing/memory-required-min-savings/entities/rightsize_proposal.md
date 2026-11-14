---
anchor: "examples.aws_finops.policy:RightsizeProposal"
kind: "entity"
---
# RightsizeProposal

<!-- generated:start -->
Field `recommendation_id`: `str`; required; constraints `[]`.

```python
class RightsizeProposal(PolicyModel):
    recommendation_id: str

    @model_validator(mode="after")
    def require_observed_evidence(self):
        candidates = required_rows("CandidatesQuery", "Fetch candidates before proposing a discovered recommendation",
                                   "rightsizing.candidates_required")
        candidate = {c.recommendation_id: c for c in candidates}.get(self.recommendation_id)
        if candidate is None:
            raise PolicyViolation("relationship", "rightsizing.discovered_candidate",
                                  "Fetch candidates before proposing a discovered recommendation", entity=self.recommendation_id)
        if candidate.target_architecture != candidate.architecture:
            raise PolicyViolation("relationship", "rightsizing.architecture", "The target must preserve CPU architecture",
                                  entity=candidate.instance_id)
        if candidate.performance_risk > 1.0:
            raise PolicyViolation("decision_rule", "rightsizing.risk", "Performance risk must be at most 1.0",
                                  entity=candidate.instance_id)
        if candidate.monthly_savings_cents <= 0:
            raise PolicyViolation("decision_rule", "rightsizing.positive_savings", "Estimated savings must be positive",
                                  entity=candidate.instance_id)
        if candidate.monthly_savings_cents < current_policy().parameters.get("minimum_savings_cents", 0):
            raise PolicyViolation("decision_rule", "rightsizing.min_savings", "Savings must meet the configured minimum")
        for metric in current_policy().parameters["required_metrics"]:
            rows = required_rows("EvidenceQuery", f"Fetch {metric} evidence for the required account before proposing",
                                 "rightsizing.metric_required", metric=metric)
            evidence = {(e.instance_id, e.metric): e for e in rows}.get((candidate.instance_id, metric))
            if evidence is None:
                raise PolicyViolation("completeness", "rightsizing.instance_evidence",
                                      f"Fetch {metric} evidence for {candidate.instance_id}; exclude it if that evidence is unavailable",
                                      entity=candidate.instance_id, details={"metric": metric})
            if evidence.days < 14:
                raise PolicyViolation("decision_rule", "rightsizing.coverage",
                                      f"At least 14 days of {metric} evidence are required", entity=candidate.instance_id)
            limit = utilization_limit(metric)
            if evidence.p95_percent > limit:
                raise PolicyViolation("decision_rule", "rightsizing.utilization",
                                      f"{metric} p95 must be at most {limit}%", entity=candidate.instance_id)
        return self
```

Rules: [rightsizing.candidates_required](../rules/rightsizing.candidates_required.md), [rightsizing.discovered_candidate](../rules/rightsizing.discovered_candidate.md), [rightsizing.architecture](../rules/rightsizing.architecture.md), [rightsizing.risk](../rules/rightsizing.risk.md), [rightsizing.positive_savings](../rules/rightsizing.positive_savings.md), [rightsizing.metric_required](../rules/rightsizing.metric_required.md), [rightsizing.instance_evidence](../rules/rightsizing.instance_evidence.md), [rightsizing.coverage](../rules/rightsizing.coverage.md), [rightsizing.utilization](../rules/rightsizing.utilization.md), [rightsizing.min_savings](../rules/rightsizing.min_savings.md)
<!-- generated:end -->

## Meaning

This page explains a code-declared part of the active policy and its connections.

## Notes (advice, not checked)

Coverage is collected days, not recency. The synthetic catalog does not establish workload performance or migration feasibility.

## Rationale

Completed observations ground the claims described here; documentation supplies meaning rather than evidence. See [rightsizing.instance_evidence](../rules/rightsizing.instance_evidence.md).

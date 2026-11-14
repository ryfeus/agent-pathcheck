---
anchor: "examples.aws_finops.policy:RightsizingPlan"
kind: "entity"
---
# RightsizingPlan

<!-- generated:start -->
Field `proposals`: `list[RightsizeProposal]`; required; constraints `[]`.
Field `monthly_savings_cents`: `int`; required; constraints `[]`.
Field `outcome`: `Literal['recommendations', 'no_eligible_recommendations']`; optional; constraints `[]`.

```python
class RightsizingPlan(PolicyModel):
    proposals: list[RightsizeProposal]
    monthly_savings_cents: int
    outcome: Literal["recommendations", "no_eligible_recommendations"] = "recommendations"

    @model_validator(mode="after")
    def match_observed_candidates(self):
        candidates = required_rows("CandidatesQuery", "Complete the scoped candidate query before building a plan",
                                   "rightsizing.candidates_required")
        for metric in current_policy().parameters["required_metrics"]:
            required_rows("EvidenceQuery", "Complete all required metric queries before selecting the top candidates",
                          "rightsizing.metric_required", metric=metric)
        by_id = {c.recommendation_id: c for c in candidates}
        ids = [by_id[p.recommendation_id].instance_id for p in self.proposals]
        if len(ids) != len(set(ids)):
            raise PolicyViolation("cardinality", "rightsizing.unique_instance", "Choose at most one recommendation per instance")
        if len(ids) > 5:
            raise PolicyViolation("cardinality", "rightsizing.maximum_proposals", "A plan must contain at most five proposals")
        top = top_candidates()
        if [p.recommendation_id for p in self.proposals] != [c.recommendation_id for c in top]:
            raise PolicyViolation("completeness", "rightsizing.top_candidates",
                                  "Select the top eligible recommendations by savings descending, then ID ascending")
        expected_outcome = "recommendations" if top else "no_eligible_recommendations"
        if self.outcome != expected_outcome:
            raise PolicyViolation("decision_rule", "rightsizing.outcome", f"Outcome must be {expected_outcome}")
        if self.monthly_savings_cents != sum(c.monthly_savings_cents for c in top):
            raise PolicyViolation("arithmetic", "rightsizing.total",
                                  "Monthly savings must equal the sum of host-observed candidate savings")
        return self
```

Rules: [rightsizing.candidates_required](../rules/rightsizing.candidates_required.md), [rightsizing.metric_required](../rules/rightsizing.metric_required.md), [rightsizing.unique_instance](../rules/rightsizing.unique_instance.md), [rightsizing.maximum_proposals](../rules/rightsizing.maximum_proposals.md), [rightsizing.top_candidates](../rules/rightsizing.top_candidates.md), [rightsizing.outcome](../rules/rightsizing.outcome.md), [rightsizing.total](../rules/rightsizing.total.md)
<!-- generated:end -->

## Meaning

The plan selects eligible recommendations in savings order and sums their observed savings.

## Notes (advice, not checked)

Coverage is collected days, not recency. The synthetic catalog does not establish workload performance or migration feasibility.

## Rationale

Completed observations ground the claims described here; documentation supplies meaning rather than evidence. See [rightsizing.instance_evidence](../rules/rightsizing.instance_evidence.md).

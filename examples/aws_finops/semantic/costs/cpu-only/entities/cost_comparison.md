---
anchor: "examples.aws_finops.policy:CostComparison"
kind: "entity"
---
# CostComparison

<!-- generated:start -->
Field `total_change_cents`: `int`; required; constraints `[]`.
Field `services`: `list[ServiceChange]`; required; constraints `[]`.

```python
class CostComparison(PolicyModel):
    total_change_cents: int
    services: list[ServiceChange]

    @model_validator(mode="after")
    def match_observed_costs(self):
        monthly = []
        for month in current_policy().parameters["months"]:
            rows = required_rows("CostQuery", f"Query {month} costs for the required account, metric and grouping",
                                 "costs.month_required", month=month, metric="NetAmortizedCost", group_by="SERVICE")
            monthly.append({row.service: row.cost_cents for row in rows})
        before, after = monthly
        changes = [(service, after.get(service, 0) - before.get(service, 0))
                   for service in before.keys() | after.keys()]
        changes.sort(key=lambda item: (-item[1], item[0]))
        if [(row.service, row.change_cents) for row in self.services] != changes:
            raise PolicyViolation("completeness", "costs.service_changes",
                                  "Service changes must match all observed differences in descending change order")
        if self.total_change_cents != sum(change for _, change in changes):
            raise PolicyViolation("arithmetic", "costs.total",
                                  "Total change must equal the sum of observed service differences")
        return self
```

Rules: [costs.month_required](../rules/costs.month_required.md), [costs.service_changes](../rules/costs.service_changes.md), [costs.total](../rules/costs.total.md)
<!-- generated:end -->

## Meaning

The comparison includes services present in either period, with absent rows contributing zero cost.

## Notes (advice, not checked)

Amounts use integer USD cents. This synthetic reference does not establish live pricing or operational suitability.

## Rationale

Completed observations ground the claims described here; documentation supplies meaning rather than evidence. See [costs.total](../rules/costs.total.md).

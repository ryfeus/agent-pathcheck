---
anchor: "examples.aws_finops.policy:CostComparisonWithCaveats"
kind: "entity"
---
# CostComparisonWithCaveats

<!-- generated:start -->
Field `total_change_cents`: `int`; required; constraints `[]`.
Field `services`: `list[ServiceChange]`; required; constraints `[]`.
Field `caveats`: `list[str]`; optional; constraints `[]`.

```python
class CostComparisonWithCaveats(CostComparison):
    caveats: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def match_required_caveats(self):
        if self.caveats != cost_caveat_ids():
            raise PolicyViolation("completeness", "costs.caveats_required",
                                  "Include exactly the required caveat IDs in sorted order without duplicates")
        return self
```

Rules: [costs.caveats_required](../rules/costs.caveats_required.md)
<!-- generated:end -->

## Meaning

Service churn requires an explicit caveat in the checked comparison.

## Notes (advice, not checked)

New or removed services may reflect migrations or one-off changes rather than usage.

## Rationale

Carry this limitation with the answer; see [required caveats](../rules/costs.caveats_required.md).

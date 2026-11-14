---
anchor: "examples.aws_finops.policy:Abstain"
kind: "entity"
---
# Abstain

<!-- generated:start -->
Field `kind`: `Literal['data_block', 'resource_limit', 'schema']`; required; constraints `[]`.
Field `rule_id`: `str`; required; constraints `[]`.
Field `detail`: `str`; required; constraints `[]`.

```python
class Abstain(PolicyModel):
    kind: Literal["data_block", "resource_limit", "schema"]
    rule_id: str
    detail: str

    def checked(self):
        policy = current_policy()
        required = policy.required_requests(policy.parameters) if policy and policy.required_requests else ()
        if not current_evidence().justifies(self.kind, self.rule_id, required):
            raise PolicyViolation("prerequisite", "abstain.unjustified",
                                  "Abstention requires a matching unresolved block on a task-required request")
        return {"kind": "abstained", "reason": self.kind, "rule_id": self.rule_id, "detail": self.detail}
```

Rules: [abstain.unjustified](../rules/abstain.unjustified.md)
<!-- generated:end -->

## Meaning

This page explains a code-declared part of the active policy and its connections.

## Notes (advice, not checked)

Coverage is collected days, not recency. The synthetic catalog does not establish workload performance or migration feasibility.

## Rationale

Completed observations ground the claims described here; documentation supplies meaning rather than evidence. See [rightsizing.instance_evidence](../rules/rightsizing.instance_evidence.md).

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

This page describes the active host contract for scoped answer claims. Only completed query evidence can support their computed values.

## Notes (advice, not checked)

Documentation explains the policy; reading it does not complete an evidence request.

## Rationale

See the [required-claim rule](../rules/claims.required_missing.md) for the checked boundary.

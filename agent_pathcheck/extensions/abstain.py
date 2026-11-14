"""Typed refusal, justified only by a blocked request the task actually needs."""

from typing import Literal

from ..contracts import PolicyModel
from ..evidence import current_evidence
from ..policy import current_policy
from ..violations import PolicyViolation


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


def validate_abstention(value, domain, validate_result):
    if type(value) is not Abstain:
        return validate_result(value)
    checked = value.checked()
    if domain.check_abstention:
        domain.check_abstention(value)
    return checked

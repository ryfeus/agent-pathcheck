"""Host-controlled contracts and bounded execution for agent-authored Python."""

from .contracts import Caveat, Operation, PolicyModel, bind_operations, validate_model_output
from .evidence import Evidence, current_evidence
from .policy import Policy, current_policy
from .report import ExecutionReport, read_report
from .rendering import Input, render_contract, tool_description
from .runtime import Domain, Execution, Invocation, run
from .violations import DataBlocked, PolicyViolation, ResourceLimitExceeded, ViolationKind

__all__ = [
    "Caveat", "DataBlocked", "Domain", "Evidence", "Execution", "ExecutionReport",
    "Input", "Invocation", "Operation", "Policy", "PolicyModel", "PolicyViolation",
    "ResourceLimitExceeded", "ViolationKind", "bind_operations", "current_evidence", "current_policy",
    "read_report", "render_contract", "run", "tool_description", "validate_model_output",
]

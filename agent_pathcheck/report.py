"""JSON report schema and a single chronological trace with derived views."""

from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter, time_ns
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Failure(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    category: Literal["syntax", "runtime", "policy", "resource_limit", "data_block", "contract", "infrastructure"] = Field(alias="class")
    detail: str
    rule_id: str | None = None
    limit: str | None = None
    configured_value: int | float | None = None


class TraceEntry(BaseModel):
    seq: int
    kind: Literal["constructor", "operation", "operation_end", "validation"]
    data: dict[str, Any]


class ReplayResult(BaseModel):
    name: str
    execution: Literal["succeeded", "failed"]
    contract_check: Literal["passed", "failed", "not_run", "not_applicable"]
    result_changed: bool | None
    signal_changed: bool | None = None
    verdict: Literal["passed", "failed", "inconclusive"] | None = None
    exemption_reason: str | None = None
    warnings: list[str] = Field(default_factory=list)
    report: dict[str, Any]


class ExecutionReport(BaseModel):
    # Domain-specific SQL traces and independent fixture verdicts are extensions.
    model_config = ConfigDict(extra="allow")
    schema_version: Literal[1, 2] = 2
    execution: Literal["succeeded", "failed"] = Field(default="failed", description="Guest execution and resource cleanup verdict.")
    contract_check: Literal["passed", "failed", "not_run", "not_applicable"] = Field(default="not_run", description="Final output contract verdict.")
    result: Any = None
    context: dict[str, Any] = Field(default_factory=dict)
    code_sha256: str | None = None
    policy_hash: str | None = None
    data_fingerprint: str | None = None
    limits: dict[str, Any] = Field(default_factory=dict)
    failure: Failure | None = Field(default=None, description="Failure class, rule_id, and the configured limit when exceeded.")
    timeline: list[TraceEntry] = Field(default_factory=list, description="Ordered operations and constructors, including caught rejections.")
    policy_calls: list[dict[str, Any]] = Field(default_factory=list)
    policy_events: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    replays: list[ReplayResult] = Field(default_factory=list, description="Counterfactual verdicts, result changes, timings, and complete child reports.")
    replay_check: Literal["not_run", "passed", "failed", "inconclusive"] = "not_run"
    require_replays: bool = False
    claim_attempts: dict[str, int] = Field(default_factory=dict)
    claim_attempts_rejected: dict[str, int] = Field(default_factory=dict)
    constructions: dict[str, int] = Field(default_factory=dict)
    duration_seconds: float = 0

    @model_validator(mode="after")
    def ordered_trace(self):
        if [event.seq for event in self.timeline] != list(range(1, len(self.timeline) + 1)):
            raise ValueError("Timeline sequence numbers must be contiguous and ordered")
        return self


def read_report(data):
    """Read current or unversioned artifacts without inventing historic ordering."""
    return ExecutionReport.model_validate({"schema_version": 1, **data})


def new_report(domain, limits, max_source_chars):
    report = ExecutionReport(context=dict(domain.context),
                             limits={**limits, "max_source_chars": max_source_chars},
                             contract_check="not_run" if domain.has_output_contract else "not_applicable")
    if domain.policy:
        report.policy_hash = domain.policy.policy_hash
        report.context.update(policy_hash=report.policy_hash, policy_version=domain.policy.version)
    result = report.model_dump(by_alias=True)
    result["policy_calls"] = TraceList(result["timeline"], "operation")
    result["policy_events"] = TraceList(result["timeline"], "constructor")
    return result


@contextmanager
def report_context(report, ledger, policy, exporter):
    from .evidence import evidence_context
    started = perf_counter()
    token = _trace.set({"timeline": report["timeline"], "exporter": exporter})
    try:
        with evidence_context(ledger, policy):
            yield
    finally:
        _trace.reset(token)
        report["duration_seconds"] = perf_counter() - started
        for field, kind in (("policy_calls", "operation"), ("policy_events", "constructor")):
            report[field] = [entry["data"] for entry in report["timeline"] if entry["kind"] == kind]


_trace = ContextVar("agent_pathcheck_trace", default=None)


class TraceList(list):
    def __init__(self, timeline, kind):
        super().__init__()
        self.timeline, self.kind = timeline, kind

    def append(self, item):
        super().append(item)
        self.timeline.append(dict(seq=len(self.timeline) + 1, kind=self.kind, data=item))


@contextmanager
def span(name, kind):
    trace = _trace.get()
    started = time_ns()
    status = "accepted"
    try:
        yield
    except Exception:
        status = "rejected"
        raise
    finally:
        if trace and trace.get("exporter"):
            try:
                trace["exporter"](dict(name=name, kind=kind, status=status,
                                       start_time=started, end_time=time_ns()))
            except Exception:
                # Telemetry must not change a policy verdict.
                pass


def operation_end(call):
    trace = _trace.get()
    if trace:
        timeline = trace["timeline"]
        timeline.append(dict(seq=len(timeline) + 1, kind="operation_end", data=dict(call)))

"""The pattern: one fresh session, private evidence, and final host validation."""

from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

from pydantic import BaseModel

from .contracts import ConstructionBudget, TracedType
from .evidence import Evidence
from .failures import set_failure
from .policy import Policy
from .report import ExecutionReport, new_report, report_context
from .violations import ResourceLimitExceeded

MAX_SOURCE_CHARS = 10_000
MONTY_LIMITS = {"max_memory": 10_000_000, "max_feed_duration_secs": 0.5,
                "max_suspensions": 30, "max_total_sleep_secs": 0.0}


@dataclass
class Invocation:
    inputs: dict[str, Any]
    functions: dict[str, Callable]
    constructors: dict[str, type[BaseModel]] = field(default_factory=dict)
    validate_result: Callable[[Any], Any] | None = None
    claims: frozenset[str] | None = None


@dataclass(frozen=True)
class Domain:
    context: dict[str, Any]
    open_invocation: Callable[..., AbstractContextManager[Invocation]]
    has_output_contract: bool = True
    policy: Policy | None = None
    initialize: Callable | None = None
    data_fingerprint: Callable[[Callable | None], str] | None = None
    allow_abstain: bool = False
    check_abstention: Callable | None = None
    max_claim_attempts: int | None = None
    max_constructions: dict[str, int] | None = None
    max_claim_rejections: int | None = None

    def __post_init__(self):
        values = [self.max_claim_attempts, self.max_claim_rejections, *(self.max_constructions or {}).values()]
        if any(value is not None and (type(value) is not int or value < 0) for value in values):
            raise ValueError("Construction limits must be nonnegative integers or None")


@dataclass
class Execution:
    report: dict
    value: Any = None  # Unverified host return value; never exposed to the guest.
    evidence: Evidence | None = field(default=None, repr=False)


def _run_once(code, domain, engine, limits, max_source_chars, span_exporter):
    report = new_report(domain, limits, max_source_chars)
    outcome = Execution(report, evidence=Evidence())
    budget = None
    with report_context(report, outcome.evidence, domain.policy, span_exporter):
        stage = "initialization"
        try:
            if not isinstance(code, str) or len(code) > max_source_chars:
                raise ResourceLimitExceeded("max_source_chars", max_source_chars,
                                           f"Source must be a string of at most {max_source_chars} characters")
            report["code_sha256"] = sha256(code.encode()).hexdigest()
            if domain.data_fingerprint:
                report["data_fingerprint"] = domain.data_fingerprint(domain.initialize)
            factory = domain.policy.build if domain.policy else domain.open_invocation
            resource = factory(report, domain.initialize) if domain.initialize else factory(report)
            with resource as invocation:
                if domain.has_output_contract != (invocation.validate_result is not None):
                    raise ValueError("Domain output-contract declaration must match its result validator")
                inputs, constructors = dict(invocation.inputs), dict(invocation.constructors)
                if domain.allow_abstain:
                    from .extensions.abstain import Abstain, validate_abstention
                    if not domain.has_output_contract:
                        raise ValueError("Abstention requires an output contract")
                    constructors["Abstain"] = Abstain
                if set(inputs) & set(constructors) or set(invocation.functions) & (set(inputs) | set(constructors)):
                    raise ValueError("Guest bindings must not shadow each other")
                if invocation.claims is None:
                    claims = {domain.policy.output_model} if domain.policy and domain.policy.output_model else set()
                else:
                    if not invocation.claims <= constructors.keys():
                        raise ValueError("Claim names must identify guest constructors")
                    claims = {constructors[name] for name in invocation.claims}
                if domain.allow_abstain:
                    claims.add(Abstain)
                if (domain.max_claim_attempts is not None or domain.max_claim_rejections is not None) and not claims:
                    raise ValueError("Claim limits require a policy output model or explicit Invocation.claims")
                budget = ConstructionBudget(report, claims, domain.max_claim_attempts, domain.max_constructions,
                                            domain.max_claim_rejections)
                report["limits"].update(max_claim_attempts=domain.max_claim_attempts,
                                        max_constructions=domain.max_constructions)
                if domain.max_claim_rejections is not None:
                    report["limits"]["max_claim_rejections"] = domain.max_claim_rejections
                inputs.update({name: TracedType(model, report["policy_events"], budget) for name, model in constructors.items()})
                with engine.pool.checkout(limits=limits) as session:
                    stage = "execution"
                    value = session.feed_run(code, inputs=inputs, external_lookup=invocation.functions,
                                             print_callback=lambda _stream, _text: None)
                if budget.failure:
                    raise budget.failure
                report["execution"] = "succeeded"
                if invocation.validate_result is not None:
                    stage = "validation"
                    try:
                        checked = (validate_abstention(value, domain, invocation.validate_result)
                                   if domain.allow_abstain else invocation.validate_result(value))
                    except Exception as exc:
                        report["contract_check"] = "failed"
                        report["errors"].append(str(exc))
                        set_failure(report, exc, stage)
                    else:
                        report.update(result=checked, contract_check="passed")
                outcome.value = value
                stage = "cleanup"
        except Exception as exc:
            report.update(execution="failed", result=None)
            outcome.value = None
            if domain.has_output_contract and (report["contract_check"] == "passed"
                    or any(event["status"] == "rejected" for event in report["policy_events"])):
                report["contract_check"] = "failed"
            report["errors"].append(f"{type(exc).__name__}: {exc}")
            set_failure(report, exc, stage)
        if budget and budget.failure:
            report.update(contract_check="failed", result=None)
            outcome.value = None
            set_failure(report, budget.failure, "execution")
    outcome.report = ExecutionReport.model_validate(report).model_dump(mode="json", by_alias=True)
    return outcome


def run(code, *, domain: Domain, limits=None, max_source_chars=MAX_SOURCE_CHARS,
        engine=None, replays=(), require_replays=False, span_exporter=None) -> Execution:
    from .extensions.engine import Engine
    replays = tuple(replays)
    if replays or require_replays:
        from .extensions.replay import apply_replays, validate_replays
        validate_replays(domain, replays)
    try:
        with (Engine() if engine is None else nullcontext(engine)) as active:
            def execute(source, target):
                return _run_once(source, target, active, {**MONTY_LIMITS, **(limits or {})},
                                 max_source_chars, span_exporter)
            outcome = execute(code, domain)
            if replays or require_replays:
                outcome = apply_replays(outcome, code, domain, replays, require_replays, execute)
        return outcome
    except Exception as exc:
        # Pool startup/shutdown must never leave a checked answer publishable.
        report = ExecutionReport(context=dict(domain.context),
                                 contract_check="failed" if domain.has_output_contract else "not_applicable",
                                 errors=[f"{type(exc).__name__}: {exc}"],
                                 failure={"class": "infrastructure", "detail": str(exc)},
                                 require_replays=require_replays)
        return Execution(report.model_dump(mode="json", by_alias=True))

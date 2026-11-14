"""The Monty/Pydantic boundary; domain rules remain in supplied models/handlers."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Literal, get_origin

from pydantic import BaseModel, ConfigDict, ValidationError
from pydantic_monty import ClassInstance, ClassType

from .evidence import Evidence, current_evidence
from .report import operation_end, span
from .violations import DataBlocked, PolicyViolation, ResourceLimitExceeded, validation_errors, validation_message


class PolicyModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, allow_inf_nan=False,
                              revalidate_instances="always")


class Caveat(PolicyModel):
    rule_id: str
    text: str


def record_rejection(model, exc, events):
    events.append({"type": model.__name__, "status": "rejected",
                   "errors": validation_errors(exc)})


class TracedType(ClassType):
    """Retain constructor rejections even when guest code catches ValueError."""

    def __init__(self, model, events, budget=None):
        primitive_fields = tuple(name for name, field in model.model_fields.items()
                                 if field.annotation in (str, int, float)
                                 or get_origin(field.annotation) is Literal)
        super().__init__(model, init=True, instance_eager_attrs=primitive_fields)
        self.events = events
        self.budget = budget

    def construct(self, args, kwargs):
        with span(self.value.__name__, "constructor"):
            return self._construct(args, kwargs)

    def _construct(self, args, kwargs):
        if self.budget:
            self.budget.before(self.value)
        try:
            instance = super().construct(args, kwargs)
        except ValidationError as exc:
            record_rejection(self.value, exc, self.events)
            if self.budget:
                self.budget.rejected(self.value)
            raise ValueError(validation_message(self.value, exc)) from None
        self.events.append({"type": self.value.__name__, "status": "accepted",
                            "value": instance.value.model_dump(mode="json")})
        return instance



class ConstructionBudget:
    """Invocation-local limits; a caught exception never clears the fatal latch."""

    def __init__(self, report, claims, max_claim_attempts, max_constructions, max_claim_rejections=None):
        self.report, self.claims = report, claims
        self.max_claim_attempts = max_claim_attempts
        self.max_constructions = dict(max_constructions or {})
        self.max_claim_rejections = max_claim_rejections
        self.failure = None

    def rejected(self, model):
        if model in self.claims:
            counts = self.report["claim_attempts_rejected"]
            counts[model.__name__] = counts.get(model.__name__, 0) + 1
            if self.max_claim_rejections is not None and sum(counts.values()) > self.max_claim_rejections:
                self.failure = self.failure or PolicyViolation(
                    "cardinality", "claims.rejection_limit", "Answer claim rejection limit exceeded")

    def before(self, model):
        name = model.__name__
        counts = self.report["constructions"]
        counts[name] = counts.get(name, 0) + 1
        if model in self.claims:
            attempts = self.report["claim_attempts"]
            attempts[name] = attempts.get(name, 0) + 1
            if self.max_claim_attempts is not None and sum(attempts.values()) > self.max_claim_attempts:
                self.failure = self.failure or PolicyViolation(
                    "cardinality", "claim.attempt_limit", "Claim construction attempt limit exceeded")
        if name in self.max_constructions and counts[name] > self.max_constructions[name]:
            self.failure = self.failure or ResourceLimitExceeded("max_constructions", self.max_constructions[name])
        if self.failure:
            self.rejected(model)
            self.report["policy_events"].append({"type": name, "status": "rejected", "errors": [
                {"type": "value_error", "loc": [], "msg": str(self.failure), "message": str(self.failure),
                 "kind": "cardinality", "rule_id": self.failure.rule_id, "entity": None, "details": {}}]})
            raise self.failure


def wrap_value(value):
    return ClassInstance(value, eager_attrs=tuple(type(value).model_fields))


@dataclass(frozen=True)
class Operation:
    """One host-registered request type, result batch, and optional evidence commit.

    execute returns raw rows. observe receives only a completely validated batch,
    including an empty batch; the domain chooses how to record its meaning.
    """

    request_type: type[BaseModel]
    result_type: type[BaseModel]
    execute: Callable[[BaseModel], Iterable[Any]]
    observe: Callable[[BaseModel, list[BaseModel]], None] | None = None
    many: bool = True
    required: bool = True
    annotate: Callable[[BaseModel, list[BaseModel]], list[Caveat]] | None = None


def bind_operations(operations, calls, events, *, request_error="query expects a supplied Pydantic query instance"):
    registry = {}
    for operation in operations:
        if operation.request_type in registry:
            raise ValueError("Each request type must identify exactly one operation")
        registry[operation.request_type] = operation

    def query(request):
        operation = registry.get(type(request))
        if operation is None:
            raise TypeError(request_error)
        call = {"type": type(request).__name__, "request": request.model_dump(mode="json"),
                "result_check": "not_run"}
        calls.append(call)
        with span(type(request).__name__, "operation"):
            try:
                return execute(operation, request, call)
            finally:
                operation_end(call)

    def execute(operation, request, call):
        try:
            ledger = current_evidence()
        except LookupError:
            ledger = Evidence()  # Standalone host-side dispatch, outside an invocation.
        try:
            rows = operation.execute(request)
            values = [operation.result_type.model_validate(row) for row in rows]
            if not operation.many and len(values) != 1:
                raise PolicyViolation("cardinality", "operation.single_result",
                                      "Operation must return exactly one result")
        except ValidationError as exc:
            message = validation_message(operation.result_type, exc)
            call.update(result_check="failed", error=message)
            record_rejection(operation.result_type, exc, events)
            errors = validation_errors(exc)
            call["failure"] = {"class": "data_block", "detail": message, "rule_id": errors[0]["rule_id"]}
            for error in errors:
                ledger._block(request, "schema", error["rule_id"], required=operation.required)
                ledger._block(request, "data_block", error["rule_id"], required=operation.required)
            raise ValueError(message) from None
        except (ResourceLimitExceeded, DataBlocked, PolicyViolation) as exc:
            kind = ("resource_limit" if isinstance(exc, ResourceLimitExceeded)
                    else "data_block" if isinstance(exc, DataBlocked) else "policy")
            failure = {"class": kind, "detail": str(exc), "rule_id": exc.rule_id}
            if isinstance(exc, ResourceLimitExceeded):
                failure.update(limit=exc.limit, configured_value=exc.value)
            call.update(result_check="failed", error=str(exc), failure=failure)
            # Exhausting a call budget deliberately is not evidence that the
            # dataset is unanswerable. Per-query data limits can justify refusal.
            if not isinstance(exc, ResourceLimitExceeded) or exc.limit != "max_queries":
                ledger._block(request, kind, exc.rule_id, required=operation.required)
            raise ValueError(str(exc)) from None
        except Exception as exc:
            call.update(result_check="failed", error=str(exc),
                        failure={"class": "infrastructure", "detail": str(exc)})
            raise
        # Annotation is trusted host logic; errors are infrastructure failures,
        # not evidence that the dataset is unanswerable. Commit only after it succeeds.
        try:
            annotations = operation.annotate(request, values) if operation.annotate else []
            if not isinstance(annotations, list):
                raise TypeError("Operation annotations must be a list of Caveat values")
            caveats = [Caveat.model_validate(c) for c in annotations]
        except Exception as exc:
            call.update(result_check="failed", error="Operation annotation failed",
                        failure={"class": "infrastructure", "detail": "Operation annotation failed"})
            raise RuntimeError("Operation annotation failed") from exc
        wrapped = [wrap_value(value) for value in values]
        ledger._commit(request, values, caveats)
        if operation.observe is not None:
            operation.observe(request, values)
        call["result_check"] = "passed"
        return wrapped if operation.many else wrapped[0]

    return query


def validate_model_output(value, model, events):
    """Revalidate a final host model against the invocation's current evidence."""
    if type(value) is not model:
        raise ValueError(f"Final expression must be a validated {model.__name__}")
    try:
        checked = model.model_validate(value.model_dump())
    except ValidationError as exc:
        record_rejection(model, exc, events)
        raise ValueError(validation_message(model, exc)) from None
    return checked.model_dump(mode="json")

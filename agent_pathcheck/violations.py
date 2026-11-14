"""Stable host-side failure labels; only concise messages cross into the guest."""

from enum import StrEnum


class ViolationKind(StrEnum):
    SCOPE = "scope"
    RELATIONSHIP = "relationship"
    PREREQUISITE = "prerequisite"
    COMPLETENESS = "completeness"
    DECISION_RULE = "decision_rule"
    ARITHMETIC = "arithmetic"
    CARDINALITY = "cardinality"
    SCHEMA = "schema"


class PolicyViolation(ValueError):
    def __init__(self, kind, rule_id, message, *, entity=None, details=None):
        super().__init__(message)
        self.kind = ViolationKind(kind)
        self.rule_id = rule_id
        self.entity = entity
        self.details = details or {}

    def as_dict(self):
        return dict(kind=self.kind.value, rule_id=self.rule_id, message=str(self),
                    entity=self.entity, details=self.details)


class ResourceLimitExceeded(ValueError):
    def __init__(self, limit, value, message=None):
        self.limit, self.value = limit, value
        self.rule_id = f"limit.{limit}"
        super().__init__(message or f"{limit} limit exceeded ({value})")


class DataBlocked(ValueError):
    def __init__(self, rule_id, message):
        self.rule_id = rule_id
        super().__init__(message)


def validation_errors(exc):
    errors = []
    for error in exc.errors(include_url=False, include_input=False):
        context = error.pop("ctx", {})
        original = context.pop("error", None)
        if isinstance(original, PolicyViolation):
            error.update(original.as_dict())
        else:
            error.update(kind="scope" if error["type"] == "literal_error" else "schema",
                         rule_id=f"pydantic.{error['type']}", entity=None, details=context)
        errors.append(error)
    return errors


def validation_message(model, exc):
    """Concise guest repair hints; exception context stays in the host report."""
    errors = validation_errors(exc)
    hints = [f"{'.'.join(map(str, error['loc'])) or 'model'}: {error['msg']} [{error['type']}] [rule {error['rule_id']}]"
             for error in errors[:3]]
    if len(errors) > 3:
        hints.append(f"{len(errors) - 3} more errors in the report")
    return f"validation error for {model.__name__}: " + "; ".join(hints)

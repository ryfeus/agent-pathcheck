"""Classify host events before matching exact resource errors from Monty 1.5.0."""

import re

from pydantic_monty import MontyRuntimeError, MontySyntaxError

from .violations import DataBlocked, PolicyViolation, ResourceLimitExceeded


# Full inner messages, pinned to pydantic-monty==1.5.0. Other errors stay runtime.
_DURATION = r"[0-9.]+(?:ns|µs|μs|us|ms|s)"
_LIMITS = (
    (MemoryError, r"memory limit exceeded: \d+ bytes > \d+ bytes", "max_memory"),
    (TimeoutError, rf"feed time limit exceeded: {_DURATION} > {_DURATION}", "max_feed_duration_secs"),
    (TimeoutError, rf"sleep limit exceeded: {_DURATION} > {_DURATION}", "max_total_sleep_secs"),
    (RuntimeError, r"suspension limit \d+ exceeded", "max_suspensions"),
)


def classify_failure(exc, report, stage):
    detail = str(exc)
    if isinstance(exc, ResourceLimitExceeded):
        return dict(class_="resource_limit", detail=detail, limit=exc.limit,
                    configured_value=exc.value, rule_id=exc.rule_id)
    if isinstance(exc, (PolicyViolation, DataBlocked)):
        return dict(class_="data_block" if isinstance(exc, DataBlocked) else "policy",
                    detail=detail, rule_id=exc.rule_id)
    if isinstance(exc, MontySyntaxError):
        return dict(class_="syntax", detail=detail)
    if isinstance(exc, MontyRuntimeError):
        inner = exc.exception()
        # Match the uncaught error, never merely the last caught rejection.
        for event in reversed(report["timeline"]):
            data = event["data"]
            if data.get("error") and data["error"] == str(inner) and data.get("failure"):
                return {"class_": data["failure"]["class"],
                        **{k: v for k, v in data["failure"].items() if k != "class"}}
            if data.get("status") == "rejected":
                for error in data.get("errors", []):
                    if error["msg"] in str(inner):
                        return dict(class_="policy", detail=detail, rule_id=error["rule_id"])
        for exception_type, pattern, limit in _LIMITS:
            if isinstance(inner, exception_type) and re.fullmatch(pattern, str(inner)):
                return dict(class_="resource_limit", detail=detail, limit=limit,
                            configured_value=report["limits"].get(limit), rule_id=f"limit.{limit}")
        return dict(class_="runtime", detail=detail)
    if stage == "validation":
        for event in reversed(report["policy_events"]):
            for error in event.get("errors", []):
                if error["msg"] in detail:
                    return dict(class_="policy", detail=detail, rule_id=error["rule_id"])
    return dict(class_="contract" if stage == "validation" else "infrastructure", detail=detail)


def set_failure(report, exc, stage):
    failure = classify_failure(exc, report, stage)
    failure["class"] = failure.pop("class_")
    report["failure"] = failure

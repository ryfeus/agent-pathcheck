"""Optional counterfactual checks, with explicit host-owned exemptions."""

from collections.abc import Callable
from dataclasses import dataclass, replace

from ..presentation import replay_requirements_met
from ..report import ExecutionReport


@dataclass(frozen=True)
class Replay:
    name: str
    perturb: Callable[[Callable], Callable]
    # Exemptions receive (domain, base execution, alternate execution).
    exemption: Callable | None = None
    # A ranking trial observes IDs; arithmetic trials observe the whole result.
    observe: Callable | None = None


def validate_replays(domain, replays):
    if replays and (domain.initialize is None or not domain.has_output_contract):
        raise ValueError("Replays require a domain initializer and semantic output contract")
    if len({replay.name for replay in replays}) != len(replays):
        raise ValueError("Replay names must be unique")


def apply_replays(outcome, code, domain, replays, required, execute):
    report = outcome.report
    report["require_replays"] = required
    if report["execution"] == "succeeded" and report["contract_check"] == "passed":
        for replay in replays:
            changed = signal = reason = None
            verdict = "failed"
            try:
                alternate = replace(domain, initialize=replay.perturb(domain.initialize))
                execution = execute(code, alternate)
                child = execution.report
                if child["execution"] == "succeeded" and child["contract_check"] == "passed":
                    changed = child["result"] != report["result"]
                    signal = (replay.observe(child["result"]) != replay.observe(report["result"])
                              if replay.observe else changed)
                    verdict = "passed" if signal else "inconclusive"
                    if not signal and replay.exemption:
                        reason = replay.exemption(domain, outcome, execution)
                        if reason is not None and (not isinstance(reason, str) or not reason.strip()):
                            raise ValueError("A replay exemption must explain the host-owned reason")
            except Exception as exc:
                verdict, reason = "failed", None
                child = ExecutionReport(errors=[str(exc)], failure={"class": "infrastructure", "detail": str(exc)}).model_dump(by_alias=True)
            report["replays"].append(dict(name=replay.name, execution=child["execution"],
                contract_check=child["contract_check"], result_changed=changed, signal_changed=signal,
                verdict=verdict, exemption_reason=reason,
                warnings=["unchanged_answer"] if changed is False else [], report=child))
    verdicts = [trial["verdict"] for trial in report["replays"]]
    report["replay_check"] = ("failed" if not verdicts or "failed" in verdicts else
                              "inconclusive" if "inconclusive" in verdicts else "passed")
    if required and not replay_requirements_met(report) and report["contract_check"] == "passed":
        report["failure"] = {"class": "contract", "detail": "Required counterfactual replays did not pass or qualify for exemption",
                             "rule_id": "replay.required"}
    outcome.report = ExecutionReport.model_validate(report).model_dump(mode="json", by_alias=True)
    return outcome

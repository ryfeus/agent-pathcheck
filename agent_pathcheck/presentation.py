"""Present only the latest host-checked result; never certify model prose."""

from copy import deepcopy


def replay_requirements_met(report):
    if report.get("schema_version", 1) == 1:
        return report.get("replay_check") == "passed"
    trials = report.get("replays", [])
    return bool(trials) and all(
        trial["execution"] == "succeeded" and trial["contract_check"] == "passed"
        and (trial.get("verdict") == "passed"
             or (trial.get("verdict") == "inconclusive" and bool(trial.get("exemption_reason"))))
        for trial in trials
    )


def validated_fields(report):
    # Domain acceptance and external fixture grading are distinct bases. A
    # completed execution alone never authorizes a checked answer.
    basis = None
    status = "not_run"
    if report:
        if report.get("contract_check", "not_applicable") == "not_applicable":
            basis, status = "fixture_answer", report.get("answer_check", "not_run")
        else:
            basis, status = "domain_contract", report["contract_check"]
    if status not in ("passed", "failed", "not_run"):
        status = "not_run"
    if status == "passed" and report.get("execution") != "succeeded":
        status = "failed"
    if status == "passed" and report.get("require_replays") and not replay_requirements_met(report):
        status = "failed"
    result = deepcopy(report["result"]) if status == "passed" else None
    answer = None
    if status == "passed":
        answer = {"basis": basis, "context": deepcopy(report["context"]), "result": deepcopy(result)}
        if report.get('answer_presentation') is not None:
            answer.update(deepcopy(report['answer_presentation']))
    return {"validation_status": status, "validation_basis": basis,
            "validated_result": result, "validated_answer": answer}

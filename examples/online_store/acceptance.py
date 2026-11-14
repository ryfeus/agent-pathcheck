"""Offline acceptance, negative probes and the retained correction story."""

import argparse
import json
from agent_pathcheck.extensions.engine import Engine
from agent_pathcheck.safety import unsupported_constructs
from .concepts import StoreConfig
from .counterfactuals import replays_for
from .policy import CASES, build_policy
from .semantic import check
from .tracer import run_checked
from .workflows import request_code, workflow


def correction_impact(*, engine=None):
    code = workflow("restock")
    old = run_checked(code, case="restock", engine=engine)
    # This is a retained previously accepted selection. It still recomputes its
    # total from evidence, isolating the policy change from stale arithmetic.
    old_ids = [p["product_id"] for p in old["result"]["proposals"]]
    query = "query(" + request_code(build_policy("restock"), 0) + ")"
    retained = (f"rows = {query}\nids = {old_ids!r}\n"
                'RestockPlan(proposals=[{"product_id": pid} for pid in ids], '
                'total_lost_revenue_cents=sum(r.lost_revenue_cents for r in rows if r.product_id in ids), outcome="recommendations")')
    rejected = run_checked(retained, case="restock", policy="minimum-monthly-sales", engine=engine)
    repaired = run_checked(workflow("restock", "minimum-monthly-sales"), case="restock", policy="minimum-monthly-sales", engine=engine)
    return dict(original=old["validated_result"], retained_answer_status=rejected["validation_status"],
                retained_failure=rejected.get("failure"), corrected=repaired["validated_result"],
                passed=old["answer_check"] == "passed" and rejected["validation_status"] == "failed" and repaired["answer_check"] == "passed")


def negative_probes():
    scope = request_code(build_policy("revenue"), 0).replace("storefront='prod'", "storefront='test'")
    comparison = workflow("category-comparison").replace("for k in keys]", 'for k in keys if k != "Outdoor"]')
    corrected = build_policy("restock", "minimum-monthly-sales")
    old_selection = (f"query({request_code(corrected, 0)})\n"
                     'RestockPlan(proposals=[{"product_id": pid} for pid in ["P-101","P-102","P-107","P-108","P-109"]], '
                     'total_lost_revenue_cents=1310000, outcome="recommendations")')
    return [
        dict(name="wrong_scope", case="revenue", code=f"query({scope})", rule="pydantic.literal_error"),
        dict(name="no_evidence", case="revenue", code="MetricResult(rows=[], total=0)", rule="evidence.required"),
        dict(name="dropped_outdoor", case="category-comparison", code=comparison, rule="store.metric_result"),
        dict(name="wrong_restock_total", case="restock", code=workflow("restock").replace("total_lost_revenue_cents=total", "total_lost_revenue_cents=total+1"), rule="store.restock_plan"),
        dict(name="old_selection_after_correction", case="restock", policy="minimum-monthly-sales", code=old_selection, rule="store.restock_eligible"),
    ]


def check_probe(probe, *, engine=None):
    report = run_checked(probe["code"], case=probe["case"], policy=probe.get("policy", "base"), engine=engine)
    # A nested Pydantic violation may appear on the constructor event rather
    # than as the first top-level failure. Match its stable typed rule ID.
    rules = {(report.get("failure") or {}).get("rule_id")}
    for event in report["policy_events"]:
        rules.update(error.get("rule_id") for error in event.get("errors", []))
    return dict(name=probe["name"], kind="negative_probe", passed=report["validation_status"] == "failed" and probe["rule"] in rules,
                expected_rule=probe["rule"], observed_rules=sorted(rule for rule in rules if rule), errors=report["errors"])


def acceptance(*, replays=False):
    checks = []
    with Engine() as engine:
        scenarios = [(case, {}) for case in CASES]
        scenarios += [("restock", {"policy": "minimum-monthly-sales"}),
                      ("return-rate", {"fixture_name": "truncated-returns"})]
        for case, options in scenarios:
            bundle = build_policy(case, options.get("policy", "base"))
            problems = check(bundle) + unsupported_constructs(bundle.contract)
            trials = replays_for(case) if replays else ()
            report = run_checked(workflow(case, options.get("policy", "base")), case=case, engine=engine,
                                 replays=trials, require_replays=bool(trials), **options)
            checks.append(dict(name=case, options=options, kind="reference", passed=(not problems and
                report["validation_status"] == "passed" and report["answer_check"] == "passed"),
                semantic_problems=problems, errors=report["errors"], answer_check=report["answer_check"],
                replay_check=report["replay_check"]))
        for case, expected in (("loyalty", ["C-future", "C-new"]), ("churn", ["C-churn", "C-window", "C-not-gold"])):
            config = StoreConfig(active_days=60)
            report = run_checked(workflow(case, config=config), case=case, config=config, engine=engine,
                                 expected_result={"customer_ids": expected})
            checks.append(dict(name=case + "-active-60", kind="shared_concept", passed=report["answer_check"] == "passed", errors=report["errors"]))
        checks += [check_probe(probe, engine=engine) for probe in negative_probes()]
        impact = correction_impact(engine=engine)
        checks.append(dict(name="correction_impact", kind="correction", passed=impact["passed"]))
    return dict(passed=all(item["passed"] for item in checks), checks=checks, correction=impact)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replays", action="store_true")
    args = parser.parse_args()
    result = acceptance(replays=args.replays)
    print(json.dumps(result, indent=2))
    raise SystemExit(not result["passed"])


if __name__ == "__main__":
    main()

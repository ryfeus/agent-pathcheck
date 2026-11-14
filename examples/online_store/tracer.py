"""Fresh SQLite/Monty executions, independent fixture grading and presentation."""

from hashlib import sha256
import json
from pathlib import Path
from agent_pathcheck import Domain, run
from agent_pathcheck.evidence import evidence_context
from agent_pathcheck.extensions.claims import render_answer
from agent_pathcheck.presentation import validated_fields
from . import fixture
from .concepts import CONCEPT_DEPENDENCIES, StoreConfig
from .policy import build_policy
from .queries import register_functions


LIMITS = {"max_suspensions": 100}


def domain_for(case="restock", policy="base", *, config=None, plan=None, refund=None,
               fixture_name="base", extra_sql="", semantic_hash=None):
    bundle = build_policy(case, policy, config=config, plan=plan, refund=refund)
    config = bundle.parameters["config"]
    if fixture_name not in ("base", "truncated-returns"):
        raise ValueError("Unknown fixture")

    def initialize(connection):
        fixture.initialize(connection, fixture=fixture_name, extra_sql=extra_sql)
        register_functions(connection, config)

    source = "".join(Path(__file__).with_name(name).read_text() for name in ("fixture.py", "data.py", "models.py", "concepts.py"))
    initialize.data_fingerprint = sha256((source + fixture_name + extra_sql + config.model_dump_json()).encode()).hexdigest()
    if semantic_hash is None:
        from .semantic import snapshot
        semantic_hash = snapshot(bundle).docs_hash
    context = dict(case=case, policy=policy, currency="USD", configuration=config.model_dump(),
                   question=question_for(bundle), docs_hash=semantic_hash,
                   normalized_plan=bundle.parameters["normalized_plan"],
                   plan_summary=bundle.parameters["plan_summary"],
                   concept_dependencies=CONCEPT_DEPENDENCIES, inherited_dependencies=bundle.parameters["dependencies"],
                   interpretation_status="unverified" if case == "composed" else "host_configured",
                   narrative_status="unverified", fixture=fixture_name)
    measure = bundle.parameters["measure_spec"]
    if measure is not None:
        context["unit"] = ("cents" if measure["measure"] == "net_revenue" else
                           "order_supplier_pairs" if measure["group_by"] == "supplier" else "orders")
        context["total_scope"] = "selected_groups" if measure["shape"] == "top_k" else "all_groups"
    composed = case == "promotion-review"
    return Domain(context, bundle.build, policy=bundle, initialize=initialize,
                  data_fingerprint=lambda init: init.data_fingerprint, allow_abstain=True,
                  max_claim_attempts=None if composed else 1, max_claim_rejections=0 if composed else None)


def question_for(bundle):
    case = bundle.parameters["case"]
    config = bundle.parameters["config"]
    questions = {
        "revenue": "What was production net revenue in the configured month?",
        "shipments": "How many production orders shipped during the last completed store-local week?",
        "category-comparison": "How did production net revenue by category change between the configured months?",
        "refund": "Is this refund request eligible, and does it require human review?",
        "restock": "Which products should be restocked? Return the exact eligible top list and its forecast total.",
        "loyalty": "Which customers newly qualify for Gold between the configured monthly snapshots?",
        "churn": "Which customers fall into the configured churn inactivity window?",
        "product-margin": "What is the configured month's margin on P-120?",
        "return-rate": "What fraction of the configured production order cohort was returned by the cutoff?",
        "promotion-review": "Why did West-region net revenue fall, and which promotions should be paused? Causal explanations remain unverified.",
        "composed": bundle.parameters["plan_summary"],
    }
    return f"{questions[case]} Reporting cutoff: {config.cutoff}; timezone: {config.timezone}."


def projection(case, result):
    if result.get("kind") == "abstained":
        return result["rule_id"]
    if case in ("revenue", "shipments"):
        return result["total"]
    if case in ("category-comparison", "composed"):
        return result["total"], [(row["key"], row["value"]) for row in result["rows"]]
    if case == "restock":
        return result["total_lost_revenue_cents"], [row["product_id"] for row in result["proposals"]]
    if case == "promotion-review":
        comparison = next(claim for claim in result["claims"].values() if "before" in claim)
        pauses = next(claim for claim in result["claims"].values() if "comparison" in claim)
        return sum(row["value"] for row in comparison["rows"]), [row["promo_id"] for row in pauses["rows"]]
    return result


EXPECTED = {
    "revenue": 36200000,
    "shipments": 2,
    "category-comparison": (5900000, [("Apparel",2500000),("Electronics",1800000),("Home",800000),
                                     ("Beauty",600000),("Books",500000),("Outdoor",-300000)]),
    "refund": {"decision":"auto_approve","reason":"within_policy"},
    "restock": (1310000, ["P-101","P-102","P-107","P-108","P-109"]),
    "loyalty": {"customer_ids":["C-future","C-new","C-window"]},
    "churn": {"customer_ids":["C-churn"]},
    "product-margin": "store.cost_missing",
    "return-rate": {"sold_units":543,"returned_units":2,"rate":2/543},
    "promotion-review": (-1100000, ["PROMO-app"]),
    "composed": (3, [("S-1",2),("S-2",1)]),
}


def run_checked(code, *, case="restock", policy="base", replays=(), require_replays=False, limits=None,
                engine=None, expected_result=None, **options):
    domain = domain_for(case, policy, **options)
    execution = run(code, domain=domain, limits={**LIMITS, **(limits or {})}, engine=engine,
                    replays=replays, require_replays=require_replays)
    report = execution.report
    report.update(answer_check="not_run", benchmark_errors=[])
    result = report["result"]
    if report["contract_check"] == "passed" and report["execution"] == "succeeded":
        expected = expected_result
        base_config = domain.policy.parameters["config"].model_dump()
        if expected is None and not options.get("extra_sql") and options.get("refund") is None and options.get("plan") is None:
            if base_config == StoreConfig().model_dump() and options.get("fixture_name", "base") == "base":
                expected = EXPECTED[case]
            elif case == "restock" and base_config == StoreConfig(minimum_monthly_sales_cents=5000).model_dump():
                expected = (1295000, ["P-101","P-102","P-107","P-108","P-110"])
            elif case == "return-rate" and options.get("fixture_name") == "truncated-returns":
                expected = "limit.max_rows"
        report["answer_check"] = "not_applicable" if expected is None else "passed" if projection(case, result) == expected else "failed"
        if report["answer_check"] == "failed":
            report["benchmark_errors"].append("Result differs from the independent fixture oracle")
        if case == "promotion-review" and result.get("kind") != "abstained":
            with evidence_context(execution.evidence, domain.policy):
                answer = domain.policy.output_model.model_validate(result)
                formatters = {domain.policy.parameters["models"][name]: lambda rows: json.dumps(rows, sort_keys=True)
                              for name in ("CategoryComparisonClaim", "PromotionPauseClaim")}
                report["answer_presentation"] = render_answer(answer, formatters)
    return {**report, **validated_fields(report)}

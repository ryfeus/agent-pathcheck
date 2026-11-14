"""Run a small FinOps workflow against a fresh SQLite fixture in real Monty."""

from hashlib import sha256
from pathlib import Path

from agent_pathcheck import Domain, run
from agent_pathcheck.presentation import validated_fields
from .policy import build_policy, modes_for
from .counterfactuals import COUNTERFACTUAL, RANKING_COUNTERFACTUAL, replays_for
from .queries import (
    create_schema,
)
from .claims import QUESTION_TEXT


FIXTURE = Path(__file__).with_name("fixture.sql")
VARIANT_SQL = """
UPDATE costs SET cost_cents = 700000
 WHERE account = 'production' AND month = '2026-10' AND service = 'RDS';
UPDATE costs SET cost_cents = 1400000
 WHERE account = 'production' AND month = '2026-10' AND service = 'EC2'
 AND metric = 'NetAmortizedCost';
UPDATE costs SET cost_cents = 90000
 WHERE account = 'production' AND month = '2026-10' AND service = 'ECS';
UPDATE recommendations SET monthly_savings_cents = 9000 WHERE recommendation_id = 'rec-web-a';
UPDATE recommendations SET monthly_savings_cents = 14000 WHERE recommendation_id = 'rec-web-b';
UPDATE recommendations SET monthly_savings_cents = 8500 WHERE recommendation_id = 'rec-worker-b';
UPDATE recommendations SET monthly_savings_cents = 6500 WHERE recommendation_id = 'rec-api-a';
INSERT INTO observations VALUES ('i-web-b', 'memory', 50.0, 14);
"""

QUESTIONS = {
    "cost-claims": QUESTION_TEXT['comparison'],
    "costs": "Explain the change in production NetAmortizedCost from September to October 2026, in USD cents. "
             "Include every service appearing in either month, treating a missing service as zero. "
             "Order services by change descending, then service name ascending, and calculate the total change.",
    "rightsizing": "Propose the top five eligible production EC2 rightsizing recommendations under the active policy "
                  "(or all eligible recommendations if fewer than five), ordered by monthly savings descending, "
                  "then recommendation ID ascending, with at most one recommendation per instance. "
                  "Calculate their combined estimated monthly savings in USD cents. "
                  "If none are eligible, return no_eligible_recommendations with an empty list and zero savings.",
}

# Independent fixture answers, never included in guest inputs or tool discovery.
COST_EXPECTED = {
    "base": (590000, [("EC2", 450000), ("EC2-Other", 60000), ("RDS", 50000),
                       ("ECS", 40000), ("S3", 30000), ("Lambda", -10000), ("CloudFront", -30000)]),
    "variant": (640000, [("RDS", 300000), ("EC2", 200000), ("ECS", 90000),
                          ("EC2-Other", 60000), ("S3", 30000), ("Lambda", -10000), ("CloudFront", -30000)]),
}
CURRENT_COST_EXPECTED = {'base': 2520000, 'variant': 2570000}
PLAN_EXPECTED = {
    ("base", "memory-required-min-savings"): (30000, ["rec-web-a", "rec-worker-a", "rec-cache-a"]),
    ("variant", "memory-required-min-savings"): (41000, ["rec-web-b", "rec-worker-a", "rec-web-a", "rec-cache-a"]),
    ("base", "cpu-only"): (50000, ["rec-web-a", "rec-web-b", "rec-worker-a", "rec-worker-b", "rec-cache-a"]),
    ("base", "memory-required"): (43000, ["rec-web-a", "rec-worker-a", "rec-cache-a", "rec-api-a", "rec-api-b"]),
    ("variant", "cpu-only"): (49500, ["rec-web-b", "rec-worker-a", "rec-web-a", "rec-worker-b", "rec-cache-a"]),
    ("variant", "memory-required"): (47500, ["rec-web-b", "rec-worker-a", "rec-web-a", "rec-cache-a", "rec-api-a"]),
}


def _grade(report, case, fixture, policy, expected_result=None):
    """Independent fixture grading, only after the engine accepts the contract."""
    value = report["result"]
    report["answer_check"] = "failed"
    if case == "cost-claims":
        from .claims import projection
        total, changes = COST_EXPECTED[fixture]
        expected = expected_result if expected_result is not None else dict(
            total_change_cents=total, services=[dict(service=s, change_cents=v) for s, v in changes])
        question = report['context']['question_id']
        if question == 'top-services' and expected_result is None:
            expected = dict(current_total_cents=CURRENT_COST_EXPECTED[fixture],
                            services=[dict(service=s, change_cents=v) for s, v in changes[:3]])
        matches = projection(value, question) == expected
    elif expected_result is not None:
        matches = value == expected_result
    elif case == "costs":
        total, changes = COST_EXPECTED[fixture]
        matches = (value["total_change_cents"] == total
                   and [(row["service"], row["change_cents"]) for row in value["services"]] == changes)
    else:
        total, ids = PLAN_EXPECTED[fixture, policy]
        matches = (value["monthly_savings_cents"] == total
                   and [p["recommendation_id"] for p in value["proposals"]] == ids)
    if policy == "costs-with-caveats" and expected_result is None:
        matches = matches and value["caveats"] == ["costs.service_churn"]
    if not any("rows" in entry for entry in report["queries"]):
        report["errors"].append("At least one successful SQLite query is required")
    elif not matches:
        report["benchmark_errors"].append("Result does not match the host-owned fixture answer")
    else:
        report["answer_check"] = "passed"


DEFAULT_BUDGET = object()


def domain_for(*, case="rightsizing", fixture="base", policy="memory-required", fixture_sql=None,
               max_claim_attempts=DEFAULT_BUDGET, max_constructions=None, semantic_hash=None,
               max_claim_rejections=DEFAULT_BUDGET, question=None):
    """Build a trusted domain without executing a workflow or grading an answer."""
    if fixture not in ("base", "variant"):
        raise ValueError("Fixture must be base or variant")
    if case not in QUESTIONS:
        raise ValueError("Case must be costs or rightsizing")
    if policy not in modes_for(case):
        raise ValueError("Policy must be cpu-only or memory-required")
    bundle = build_policy(case, policy, question=question)
    if case == 'cost-claims':
        question = bundle.parameters['question']
    if semantic_hash is None:
        from agent_pathcheck.semantic import SemanticReader
        from .semantic import directory_for
        semantic_hash = SemanticReader(directory_for(case, policy, question)).docs_hash
    context = {"docs_hash": semantic_hash, "case": case,
               "question": QUESTION_TEXT[question] if case == 'cost-claims' else QUESTIONS[case],
               "account": "production", "currency": "USD", "unit": "cents",
               "periods": ["2026-09", "2026-10"] if case in ("costs", "cost-claims") else None,
               "policy": policy, "experiment": "typed_query"}
    source = FIXTURE.read_text() if fixture_sql is None else fixture_sql

    def initialize(connection):
        create_schema(connection)
        connection.executescript(source)
        if fixture == "variant":
            connection.executescript(VARIANT_SQL)

    initialize.data_fingerprint = sha256((source + (VARIANT_SQL if fixture == "variant" else "")).encode()).hexdigest()
    if case == 'cost-claims':
        context['question_id'] = question
    if max_claim_attempts is DEFAULT_BUDGET:
        max_claim_attempts = None if case == "cost-claims" else 1
    if max_claim_rejections is DEFAULT_BUDGET:
        max_claim_rejections = 0 if case == "cost-claims" else None
    domain = Domain(context, bundle.build, policy=bundle, initialize=initialize,
                    data_fingerprint=database_fingerprint, allow_abstain=True,
                    max_claim_attempts=max_claim_attempts, max_constructions=max_constructions,
                    max_claim_rejections=max_claim_rejections)
    return domain


def run_checked(code, *, case="rightsizing", fixture="base", policy="memory-required",
                fixture_sql=None, expected_result=None, engine=None, replays=(), require_replays=False,
                limits=None, max_claim_attempts=DEFAULT_BUDGET, max_constructions=None, semantic_hash=None,
                max_claim_rejections=DEFAULT_BUDGET, question=None):
    """Only code is agent-owned; optional benchmark data/oracles stay on the host."""
    domain = domain_for(case=case, fixture=fixture, policy=policy, fixture_sql=fixture_sql,
                        max_claim_attempts=max_claim_attempts, max_constructions=max_constructions,
                        semantic_hash=semantic_hash, max_claim_rejections=max_claim_rejections, question=question)
    bundle = domain.policy
    outcome = run(code, domain=domain, limits={**LIMITS, **(limits or {})}, engine=engine,
                  replays=replays, require_replays=require_replays)
    report = outcome.report
    report.update(answer_check="not_run", policy=policy, benchmark_errors=[])
    report.setdefault("queries", [])
    if (report["execution"] == "succeeded" and report["contract_check"] == "passed"
            and report["result"].get("kind") != "abstained"):
        _grade(report, case, fixture, policy, expected_result)
        if policy == "costs-with-caveats":
            report["context"]["caveats"] = [dict(rule_id=key, text=bundle.rules[key].why)
                                             for key in report["result"]["caveats"]]
    if (case == 'cost-claims' and validated_fields(report)['validation_status'] == 'passed'
            and report['result'].get('kind') != 'abstained'):
        from agent_pathcheck.evidence import evidence_context
        from agent_pathcheck.extensions.claims import render_answer
        from .claims import formatters
        with evidence_context(outcome.evidence, bundle):
            answer = bundle.output_model.model_validate(report['result'])
            report['answer_presentation'] = render_answer(answer, formatters(bundle.parameters['models']))
    return {**report, **validated_fields(report)}


LIMITS = {"max_suspensions": 80}


def database_fingerprint(initialize):
    return initialize.data_fingerprint


# Handwritten examples, not included in the agent's context.
COST_WORKFLOW = '''\
before = query(CostQuery(account=account, month=previous_period,
                         metric="NetAmortizedCost", group_by="SERVICE"))
after = query(CostQuery(account=account, month=period,
                        metric="NetAmortizedCost", group_by="SERVICE"))
changes = {}
for row in before:
    changes[row.service] = -row.cost_cents
for row in after:
    changes[row.service] = changes.get(row.service, 0) + row.cost_cents
ordered = sorted(changes, key=lambda service: (-changes[service], service))
CostComparison(total_change_cents=sum(changes.values()), services=[
    ServiceChange(service=service, change_cents=changes[service]) for service in ordered
])
'''


def rightsizing_workflow(policy="memory-required"):
    metrics = ("cpu", "memory") if policy != "cpu-only" else ("cpu",)
    return f'''\
candidates = query(CandidatesQuery(account=account))
for metric in {metrics!r}:
    query(EvidenceQuery(account=account, metric=metric))
ordered = sorted(candidates, key=lambda c: (-c.monthly_savings_cents, c.recommendation_id))
proposals = []
seen_instances = set()
total = 0
for candidate in ordered:
    if candidate.instance_id in seen_instances:
        continue
    try:
        proposal = RightsizeProposal(recommendation_id=candidate.recommendation_id)
    except ValueError:
        continue
    proposals.append(proposal)
    seen_instances.add(candidate.instance_id)
    total += candidate.monthly_savings_cents
    if len(proposals) == 5:
        break
RightsizingPlan(proposals=proposals, monthly_savings_cents=total,
               outcome="recommendations" if proposals else "no_eligible_recommendations")
'''

"""Executable FinOps rules over invocation-local, host-validated evidence."""

from contextlib import ExitStack, contextmanager
import sqlite3
from typing import Literal

from pydantic import Field, create_model, model_validator

from agent_pathcheck import (
    DataBlocked, Invocation, Operation, Policy, PolicyModel, PolicyViolation, bind_operations,
    current_evidence, current_policy, validate_model_output,
)
from agent_pathcheck.extensions.abstain import Abstain
from agent_pathcheck.extensions.sqlalchemy import request_model, result_model
from agent_pathcheck.adapters.sqlite import open_database
from agent_pathcheck.rendering import Input, render_contract
from .rules import rules_for
from .queries import (CANDIDATE_COLUMNS, EVIDENCE_COLUMNS, instances, observations,
                      candidates_statement, cost_statement, evidence_statement, execute_statement)


class ServiceCost(PolicyModel):
    service: str
    cost_cents: int


class ServiceChange(PolicyModel):
    service: str
    change_cents: int


Candidate = result_model("Candidate", CANDIDATE_COLUMNS, refinements={
    "performance_risk": (float, Field(ge=0, le=4, allow_inf_nan=False)),
})
Evidence = result_model("Evidence", EVIDENCE_COLUMNS, refinements={
    "metric": (Literal["cpu", "memory"], ...),
    "p95_percent": (float, Field(ge=0, le=100, allow_inf_nan=False)),
    "days": (int, Field(ge=0)),
})


def required_rows(name, message, rule_id, **fields):
    params = current_policy().parameters
    request = params["models"][name](account=params["account"], **fields)
    if not current_evidence().completed(request):
        raise PolicyViolation("prerequisite", rule_id, message)
    return current_evidence().results(request)


class CostComparison(PolicyModel):
    total_change_cents: int
    services: list[ServiceChange]

    @model_validator(mode="after")
    def match_observed_costs(self):
        monthly = []
        for month in current_policy().parameters["months"]:
            rows = required_rows("CostQuery", f"Query {month} costs for the required account, metric and grouping",
                                 "costs.month_required", month=month, metric="NetAmortizedCost", group_by="SERVICE")
            monthly.append({row.service: row.cost_cents for row in rows})
        before, after = monthly
        changes = [(service, after.get(service, 0) - before.get(service, 0))
                   for service in before.keys() | after.keys()]
        changes.sort(key=lambda item: (-item[1], item[0]))
        if [(row.service, row.change_cents) for row in self.services] != changes:
            raise PolicyViolation("completeness", "costs.service_changes",
                                  "Service changes must match all observed differences in descending change order")
        if self.total_change_cents != sum(change for _, change in changes):
            raise PolicyViolation("arithmetic", "costs.total",
                                  "Total change must equal the sum of observed service differences")
        return self


def cost_caveat_ids():
    monthly = []
    for month in current_policy().parameters["months"]:
        rows = required_rows("CostQuery", "Complete both monthly reads before deriving caveats",
                             "costs.month_required", month=month, metric="NetAmortizedCost", group_by="SERVICE")
        monthly.append({row.service for row in rows})
    return ["costs.service_churn"] if monthly[0] != monthly[1] else []


class CostComparisonWithCaveats(CostComparison):
    caveats: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def match_required_caveats(self):
        if self.caveats != cost_caveat_ids():
            raise PolicyViolation("completeness", "costs.caveats_required",
                                  "Include exactly the required caveat IDs in sorted order without duplicates")
        return self


class RightsizeProposal(PolicyModel):
    recommendation_id: str

    @model_validator(mode="after")
    def require_observed_evidence(self):
        candidates = required_rows("CandidatesQuery", "Fetch candidates before proposing a discovered recommendation",
                                   "rightsizing.candidates_required")
        candidate = {c.recommendation_id: c for c in candidates}.get(self.recommendation_id)
        if candidate is None:
            raise PolicyViolation("relationship", "rightsizing.discovered_candidate",
                                  "Fetch candidates before proposing a discovered recommendation", entity=self.recommendation_id)
        if candidate.target_architecture != candidate.architecture:
            raise PolicyViolation("relationship", "rightsizing.architecture", "The target must preserve CPU architecture",
                                  entity=candidate.instance_id)
        if candidate.performance_risk > 1.0:
            raise PolicyViolation("decision_rule", "rightsizing.risk", "Performance risk must be at most 1.0",
                                  entity=candidate.instance_id)
        if candidate.monthly_savings_cents <= 0:
            raise PolicyViolation("decision_rule", "rightsizing.positive_savings", "Estimated savings must be positive",
                                  entity=candidate.instance_id)
        if candidate.monthly_savings_cents < current_policy().parameters.get("minimum_savings_cents", 0):
            raise PolicyViolation("decision_rule", "rightsizing.min_savings", "Savings must meet the configured minimum")
        for metric in current_policy().parameters["required_metrics"]:
            rows = required_rows("EvidenceQuery", f"Fetch {metric} evidence for the required account before proposing",
                                 "rightsizing.metric_required", metric=metric)
            evidence = {(e.instance_id, e.metric): e for e in rows}.get((candidate.instance_id, metric))
            if evidence is None:
                raise PolicyViolation("completeness", "rightsizing.instance_evidence",
                                      f"Fetch {metric} evidence for {candidate.instance_id}; exclude it if that evidence is unavailable",
                                      entity=candidate.instance_id, details={"metric": metric})
            if evidence.days < 14:
                raise PolicyViolation("decision_rule", "rightsizing.coverage",
                                      f"At least 14 days of {metric} evidence are required", entity=candidate.instance_id)
            limit = utilization_limit(metric)
            if evidence.p95_percent > limit:
                raise PolicyViolation("decision_rule", "rightsizing.utilization",
                                      f"{metric} p95 must be at most {limit}%", entity=candidate.instance_id)
        return self


def utilization_limit(metric):
    return 60.0 if metric == "cpu" else 80.0


def top_candidates():
    """Recompute selection from evidence; also used for trusted replay exemptions."""
    candidates = required_rows("CandidatesQuery", "Complete the scoped candidate query before building a plan",
                               "rightsizing.candidates_required")
    for metric in current_policy().parameters["required_metrics"]:
        required_rows("EvidenceQuery", "Complete all required metric queries before selecting the top candidates",
                      "rightsizing.metric_required", metric=metric)
    eligible, seen = [], set()
    for candidate in sorted(candidates, key=lambda c: (-c.monthly_savings_cents, c.recommendation_id)):
        try:
            RightsizeProposal(recommendation_id=candidate.recommendation_id)
        except ValueError:
            continue
        if candidate.instance_id not in seen:
            eligible.append(candidate)
            seen.add(candidate.instance_id)
    return eligible[:5]


class RightsizingPlan(PolicyModel):
    proposals: list[RightsizeProposal]
    monthly_savings_cents: int
    outcome: Literal["recommendations", "no_eligible_recommendations"] = "recommendations"

    @model_validator(mode="after")
    def match_observed_candidates(self):
        candidates = required_rows("CandidatesQuery", "Complete the scoped candidate query before building a plan",
                                   "rightsizing.candidates_required")
        for metric in current_policy().parameters["required_metrics"]:
            required_rows("EvidenceQuery", "Complete all required metric queries before selecting the top candidates",
                          "rightsizing.metric_required", metric=metric)
        by_id = {c.recommendation_id: c for c in candidates}
        ids = [by_id[p.recommendation_id].instance_id for p in self.proposals]
        if len(ids) != len(set(ids)):
            raise PolicyViolation("cardinality", "rightsizing.unique_instance", "Choose at most one recommendation per instance")
        if len(ids) > 5:
            raise PolicyViolation("cardinality", "rightsizing.maximum_proposals", "A plan must contain at most five proposals")
        top = top_candidates()
        if [p.recommendation_id for p in self.proposals] != [c.recommendation_id for c in top]:
            raise PolicyViolation("completeness", "rightsizing.top_candidates",
                                  "Select the top eligible recommendations by savings descending, then ID ascending")
        expected_outcome = "recommendations" if top else "no_eligible_recommendations"
        if self.outcome != expected_outcome:
            raise PolicyViolation("decision_rule", "rightsizing.outcome", f"Outcome must be {expected_outcome}")
        if self.monthly_savings_cents != sum(c.monthly_savings_cents for c in top):
            raise PolicyViolation("arithmetic", "rightsizing.total",
                                  "Monthly savings must equal the sum of host-observed candidate savings")
        return self


def operations_for(models, sql_query):
    if "CostQuery" in models:
        return [Operation(models["CostQuery"], ServiceCost,
                          lambda request: execute_statement(sql_query, cost_statement(request)))]
    return [Operation(models["CandidatesQuery"], Candidate,
                      lambda request: execute_statement(sql_query, candidates_statement(request))),
            Operation(models["EvidenceQuery"], Evidence,
                      lambda request: execute_statement(sql_query, evidence_statement(request)))]


def required_requests(parameters):
    models, account = parameters["models"], parameters["account"]
    if "CostQuery" in models:
        return [models["CostQuery"](account=account, month=month, metric="NetAmortizedCost", group_by="SERVICE")
                for month in parameters["months"]]
    return [models["CandidatesQuery"](account=account),
            *[models["EvidenceQuery"](account=account, metric=metric)
              for metric in parameters["required_metrics"]]]


def modes_for(case):
    if case == "cost-claims":
        return ("cpu-only", "memory-required")
    return ("cpu-only", "memory-required", "costs-with-caveats") if case == "costs" else ("cpu-only", "memory-required", "memory-required-min-savings")


def build_policy(case, mode, *, question=None):
    if case not in ("costs", "rightsizing", "cost-claims"):
        raise ValueError("Case must be costs, rightsizing or cost-claims")
    if mode not in modes_for(case):
        raise ValueError("Policy must be cpu-only or memory-required")
    if question is not None and case != 'cost-claims':
        raise ValueError('Question selection is available for cost-claims')
    question = (question or 'comparison') if case == 'cost-claims' else None
    account, months = "production", ("2026-09", "2026-10")
    metrics = ("cpu", "memory") if mode != "cpu-only" else ("cpu",)
    if case in ("costs", "cost-claims"):
        request = create_model("CostQuery", __base__=PolicyModel, account=(Literal[account], ...),
                               month=(Literal[months], ...), metric=(Literal["NetAmortizedCost"], ...),
                               group_by=(Literal["SERVICE"], ...))
        constructors = (request, ServiceChange, CostComparison)
        output = CostComparisonWithCaveats if mode == "costs-with-caveats" else CostComparison
        constructors = (request, ServiceChange, output)
        if case == "cost-claims":
            from agent_pathcheck.extensions.claims import answer_model
            from .claims import claim_types, required_claims
            types = claim_types(request, ServiceChange)
            required = required_claims({'CostQuery': request, **{cls.__name__: cls for cls in types}},
                                       question, account, months)
            output = answer_model(types, required=required)
            constructors = (request, ServiceChange, *types, output)
    else:
        candidates = request_model("CandidatesQuery", [instances.c.account], scope={"account": account})
        evidence = request_model("EvidenceQuery", [instances.c.account, observations.c.metric],
                                 scope={"account": account, "metric": metrics})
        constructors = (candidates, evidence, RightsizeProposal, RightsizingPlan)
        output = RightsizingPlan
    models = {cls.__name__: cls for cls in (*constructors, Abstain)}
    if mode == "costs-with-caveats":
        models["CostComparison"] = models.pop("CostComparisonWithCaveats")
    inputs = dict(account=Input(Literal[account], account), period=Input(Literal[months[1]], months[1]),
                  previous_period=Input(Literal[months[0]], months[0]))
    if case == "rightsizing":
        inputs["required_metrics"] = Input(tuple[str, ...], metrics)
    if mode == "memory-required-min-savings":
        inputs["minimum_savings_cents"] = Input(int, 8000)
    helpers = (required_rows, utilization_limit, top_candidates) if case == "rightsizing" else (required_rows,)
    if mode == "costs-with-caveats":
        helpers += (CostComparison, cost_caveat_ids)
    if case == "cost-claims":
        from agent_pathcheck.extensions.claims import AnswerEnvelope, Claim, settle
        from .claims import claim_input
        helpers = (Claim, settle, claim_input, AnswerEnvelope)
    source = render_contract(operations_for(models, None), models, inputs, extra_sources=helpers)
    if case == "cost-claims":
        source = 'from typing import ClassVar\nfrom agent_pathcheck.extensions.claims import PLACEHOLDER\n' + source
        source += '\n# This question requires: ' + '; '.join(spec.description() for spec in required)

    @contextmanager
    def open_invocation(report, initialize):
        report["queries"] = []
        with ExitStack() as stack:
            database_query = None

            def sql_query(sql, params=()):
                nonlocal database_query
                if database_query is None:
                    try:
                        database_query = stack.enter_context(open_database(initialize, report["queries"]))
                    except sqlite3.IntegrityError as exc:
                        raise DataBlocked("data.integrity", str(exc)) from None
                return database_query(sql, params)

            query = bind_operations(operations_for(models, sql_query), report["policy_calls"], report["policy_events"])
            yield Invocation({name: item.value for name, item in inputs.items()}, {"query": query}, models,
                             lambda value: validate_model_output(value, output, report["policy_events"]),
                             frozenset(('CostTotal', 'CostDelta', 'ServiceChanges', 'Answer'))
                             if case == "cost-claims" else None)

    return Policy(mode, "3", open_invocation, output,
                  dict(account=account, months=months, required_metrics=metrics, models=models,
                       minimum_savings_cents=8000 if mode == "memory-required-min-savings" else 0,
                       **({'request_types': (request,), 'question': question, 'required_claims': required}
                          if case == 'cost-claims' else {})), source,
                  required_requests=required_requests, rules=rules_for(case, mode))

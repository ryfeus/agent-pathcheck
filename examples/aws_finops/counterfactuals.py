"""Trusted data perturbations and evidence-derived exemptions for this domain."""

from hashlib import sha256

from agent_pathcheck.evidence import evidence_context
from agent_pathcheck.extensions.replay import Replay
from .policy import top_candidates


def perturb(initialize, name, update):
    def changed(connection):
        initialize(connection)
        update(connection)
    changed.data_fingerprint = sha256((initialize.data_fingerprint + name).encode()).hexdigest()
    return changed


def arithmetic_data(initialize):
    def update(connection):
        connection.execute("UPDATE recommendations SET monthly_savings_cents = monthly_savings_cents * 2 "
                           "WHERE monthly_savings_cents > 0")
        # One scoped row changes one service delta, including before-only data.
        connection.execute("UPDATE costs SET cost_cents = cost_cents + 1 WHERE rowid = "
                           "(SELECT MIN(rowid) FROM costs WHERE account = 'production' "
                           "AND metric = 'NetAmortizedCost' AND month IN ('2026-09', '2026-10'))")
    return perturb(initialize, "arithmetic-v2", update)


def ranking_data(initialize):
    def update(connection):
        rows = connection.execute("SELECT recommendation_id FROM recommendations "
                                  "WHERE monthly_savings_cents > 0 "
                                  "ORDER BY monthly_savings_cents DESC, recommendation_id ASC").fetchall()
        # Reverse the complete positive ranking, including ties, using exact ints.
        connection.executemany("UPDATE recommendations SET monthly_savings_cents = ? WHERE recommendation_id = ?",
                               [(rank, row[0]) for rank, row in enumerate(rows, 1)])
    return perturb(initialize, "ranking-v1", update)


def observed_selection(domain, execution):
    with evidence_context(execution.evidence, domain.policy):
        return [candidate.recommendation_id for candidate in top_candidates()]


def arithmetic_exemption(domain, base, alternate):
    if domain.context['case'] == 'cost-claims':
        observed = []
        for execution in (base, alternate):
            with evidence_context(execution.evidence, domain.policy):
                observed.append([spec.claim_type.model_validate({**spec.sources, **spec.qualifiers}).result()
                                 for spec in domain.policy.parameters['required_claims']])
        if observed[0] == observed[1]:
            return 'Host recomputation establishes unchanged values for every required scoped answer claim.'
        return None
    if domain.context["case"] == "costs":
        requests = domain.policy.required_requests(domain.policy.parameters)
        if all(not base.evidence.require(request) for request in requests):
            return "Both required monthly cost queries completed with no rows."
    elif not observed_selection(domain, base) and not observed_selection(domain, alternate):
        return "Complete required evidence establishes no eligible recommendations in either dataset."
    return None


def ranking_exemption(domain, base, alternate):
    if observed_selection(domain, base) == observed_selection(domain, alternate):
        return "Host evidence establishes that this perturbation leaves the ordered eligible selection invariant."
    return None


def proposal_ids(result):
    return [proposal["recommendation_id"] for proposal in result["proposals"]]


COUNTERFACTUAL = Replay("changed-costs-and-savings", arithmetic_data, exemption=arithmetic_exemption)
RANKING_COUNTERFACTUAL = Replay("changed-recommendation-ranking", ranking_data,
                               exemption=ranking_exemption, observe=proposal_ids)


def replays_for(case):
    return (COUNTERFACTUAL, RANKING_COUNTERFACTUAL) if case == "rightsizing" else (COUNTERFACTUAL,)

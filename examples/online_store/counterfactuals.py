"""Host-owned data perturbations, with no model calls."""

from hashlib import sha256
from agent_pathcheck.evidence import evidence_context
from agent_pathcheck.extensions.replay import Replay
from .data import validate_integrity
from .policy import eligible_stock


def perturb(sql):
    def wrap(initialize):
        def changed(connection):
            initialize(connection)
            connection.executescript(sql)
            validate_integrity(connection)
        changed.data_fingerprint = sha256((initialize.data_fingerprint + sql).encode()).hexdigest()
        return changed
    return wrap


def empty_ranking_exemption(domain, before, after):
    for execution in (before, after):
        with evidence_context(execution.evidence, domain.policy):
            if eligible_stock():
                return None
    return "Both complete evidence ledgers independently contain no eligible restock products."


def replays_for(case):
    if case in ("revenue", "category-comparison"):
        return (Replay("revenue-arithmetic", perturb("UPDATE order_lines SET unit_price_cents=unit_price_cents+100 WHERE line_id='L-balance-2026-10-Apparel';")),)
    if case == "restock":
        return (
            Replay("forecast-arithmetic", perturb("UPDATE forecasts SET lost_revenue_cents=lost_revenue_cents*2;"), exemption=empty_ranking_exemption),
            Replay("restock-ranking", perturb("UPDATE forecasts SET lost_revenue_cents=900000 WHERE product_id='P-110';"),
                   observe=lambda result: [row["product_id"] for row in result["proposals"]], exemption=empty_ranking_exemption),
        )
    return ()

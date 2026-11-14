"""Run submitted SQL and Python math against one host-owned SQLite fixture."""

from contextlib import contextmanager
from dataclasses import dataclass
import json
import math
from pathlib import Path
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent_pathcheck import Domain, Invocation, Policy, run
from agent_pathcheck.rules import shared_rules
from agent_pathcheck.adapters.sqlite import (
    MAX_QUERIES, MAX_ROWS, MAX_SQL_BYTES, MAX_SQL_VALUE_BYTES,
    SQL_PROGRESS_INTERVAL, SQL_INSTRUCTION_BUDGET,
    authorize_read_only as _authorize, make_query as _make_query, open_database,
)
from agent_pathcheck.presentation import validated_fields
from agent_pathcheck.runtime import MAX_SOURCE_CHARS, MONTY_LIMITS


SCHEMA = """CREATE TABLE sales (
    period TEXT NOT NULL,
    revenue_cents INTEGER NOT NULL,
    cogs_cents INTEGER NOT NULL
);"""

BASE_ROWS = (
    ("2026-10", 6_000_000, 4_000_000),
    ("2026-10", 4_000_000, 2_760_000),
    ("2026-09", 5_000_000, 3_100_000),
    ("2026-09", 3_000_000, 1_900_000),
)
VARIANT_ROWS = (
    ("2026-10", 7_000_000, 5_000_000),
    ("2026-10", 5_000_000, 4_000_000),
    ("2026-09", 6_000_000, 3_600_000),
    ("2026-09", 3_000_000, 1_800_000),
)


@dataclass(frozen=True)
class Task:
    question: str
    expected: float
    period: str = "2026-10"
    previous_period: str = "2026-09"
    include_previous_period: bool = False
    unit: str = "number"


# Independent, host-owned answers, never passed into the guest or database.
BASE_TASKS = {
    "revenue": Task("What was October 2026 revenue in dollars?", 100000.0, unit="USD"),
    "margin": Task("What was October 2026 gross margin, as a ratio?", 0.324, unit="ratio"),
    "change": Task(
        "How did gross margin change from September to October 2026, "
        "in percentage points?", -5.1, include_previous_period=True, unit="percentage_points"
    ),
}
VARIANT_TASKS = {
    "revenue": Task(BASE_TASKS["revenue"].question, 120000.0, unit="USD"),
    "margin": Task(BASE_TASKS["margin"].question, 0.25, unit="ratio"),
    "change": Task(BASE_TASKS["change"].question, -15.0, include_previous_period=True, unit="percentage_points"),
}

# Handwritten smoke submissions, not agent-generated evaluation results.
REFERENCE_PROGRAMS = {
    "revenue": '''\
rows = query(
    "SELECT SUM(revenue_cents) AS revenue FROM sales WHERE period = ?",
    (period,),
)
rows[0]["revenue"] / 100
''',
    "margin": '''\
rows = query(
    "SELECT SUM(revenue_cents) AS revenue, SUM(cogs_cents) AS cogs "
    "FROM sales WHERE period = :period",
    {"period": period},
)
r = rows[0]
(r["revenue"] - r["cogs"]) / r["revenue"]
''',
    "change": '''\
rows = query(
    "SELECT period, SUM(revenue_cents) AS revenue, SUM(cogs_cents) AS cogs "
    "FROM sales WHERE period IN (?, ?) GROUP BY period",
    (period, previous_period),
)
margins = {}
for r in rows:
    margins[r["period"]] = (r["revenue"] - r["cogs"]) / r["revenue"]
(margins[period] - margins[previous_period]) * 100
''',
}
WRONG_MONTH_PROGRAM = REFERENCE_PROGRAMS["revenue"].replace(
    "(period,)", "(previous_period,)"
)


def _grade(report, value, expected, *, evidence=None, request_type=None):
    report["answer_check"] = "failed"
    if type(value) not in (int, float):
        report["errors"].append("Result must be a finite integer or float, excluding booleans")
        return
    try:
        if not math.isfinite(value):
            raise ValueError("Result must be finite")
        matches = math.isclose(value, expected, rel_tol=0.0, abs_tol=1e-9)
    except (OverflowError, TypeError, ValueError) as exc:
        report["errors"].append(f"Invalid numeric result: {exc}")
        return

    report["result"] = value
    if request_type is not None and not evidence.find(request_type):
        report["errors"].append("At least one successful typed query with validated results is required")
    elif "policy_calls" not in report and not any("rows" in entry for entry in report["queries"]):
        report["errors"].append("At least one successful SQL query is required")
    elif not matches:
        report["errors"].append("Result does not match the host-owned expected answer")
    else:
        report["answer_check"] = "passed"


def run_checked(task: Task, code: str, *, policy, rows=BASE_ROWS) -> dict:
    """Governed entry point: only code is agent-owned; policy must be explicit."""
    if policy is None:
        raise ValueError("A typed policy is required; use run_raw_sql_baseline for the baseline")
    return _run(task, code, rows=rows, policy=policy)


def run_raw_sql_baseline(task: Task, code: str, *, rows=BASE_ROWS) -> dict:
    """Explicit comparison experiment: the submitted workflow writes raw SQL."""
    return _run(task, code, rows=rows, policy=None)


def _run(task, code, *, rows, policy):
    # The sales experiment deliberately has no semantic output contract. Its
    # numeric oracle remains an example-level evaluation step after execution.
    context = {"question": task.question, "unit": task.unit,
               "periods": [task.previous_period, task.period] if task.include_previous_period else [task.period],
               "experiment": "raw_sql_baseline" if policy is None else "typed_query"}
    queries = []

    def initialize(connection):
        connection.execute(SCHEMA)
        connection.executemany("INSERT INTO sales VALUES (?, ?, ?)", rows)

    @contextmanager
    def open_invocation(report):
        with open_database(initialize, queries) as sql_query:
            inputs = {"question": task.question, "period": task.period,
                      "previous_period": task.previous_period}
            constructors = {}
            if policy is None:
                inputs["schema"] = SCHEMA
                query = sql_query
            else:
                constructors["SalesQuery"] = policy.request_type
                query = policy.bind(sql_query, report["policy_calls"], report["policy_events"])
            yield Invocation(inputs, {"query": query}, constructors)

    bundle = (Policy(policy.aggregation, "2", open_invocation, None, contract=policy.contract,
                     rules={k: v for k, v in shared_rules().items() if k.startswith("pydantic.")})
              if policy else None)
    outcome = run(code, domain=Domain(context, open_invocation, has_output_contract=False, policy=bundle))
    report = outcome.report
    report.update(answer_check="not_run", queries=queries)
    if policy is not None:
        report["policy"] = policy.aggregation
        # Preserve the sales report's established flat request trace.
        report["policy_calls"] = [
            {**call["request"], **{key: value for key, value in call.items()
                                  if key not in ("request", "type")}}
            for call in report["policy_calls"]
        ]
    else:
        del report["policy_calls"]
    if report["execution"] == "succeeded":
        _grade(report, outcome.value, task.expected, evidence=outcome.evidence,
               request_type=policy.request_type if policy else None)
        if report["answer_check"] == "failed":
            report["failure"] = {"class": "contract", "detail": report["errors"][-1], "rule_id": "sales.fixture_answer"}
    return {**report, **validated_fields(report)}


def main():
    """Print four handwritten smoke-test reports, one JSON object per line."""
    for name, task in BASE_TASKS.items():
        print(json.dumps(run_raw_sql_baseline(task, REFERENCE_PROGRAMS[name]), allow_nan=False))
    print(json.dumps(run_raw_sql_baseline(BASE_TASKS["revenue"], WRONG_MONTH_PROGRAM), allow_nan=False))


if __name__ == "__main__":
    main()

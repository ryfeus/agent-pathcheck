"""Executable Python query contracts for the reference workflow.

The host chooses a Pydantic type. Monty exposes its constructor, and query()
accepts only instances of that type. No separate policy DSL or model judge.
"""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, create_model

from agent_pathcheck import Operation, PolicyModel, bind_operations, render_contract


class SalesTotals(PolicyModel):
    revenue_cents: int
    cogs_cents: int


class Sale(PolicyModel):
    revenue_cents: int
    cogs_cents: int


@dataclass(frozen=True)
class QueryPolicy:
    request_type: type[BaseModel]
    result_type: type[BaseModel]

    @property
    def aggregation(self):
        return self.request_type.model_fields["aggregation"].annotation.__args__[0]

    @property
    def contract(self):
        return render_contract(self.operations(None), {"SalesQuery": self.request_type}, {})

    def operations(self, sql_query):
        def execute(request):
            if self.aggregation == "sum":
                return sql_query(
                    "SELECT COALESCE(SUM(revenue_cents), 0) AS revenue_cents, "
                    "COALESCE(SUM(cogs_cents), 0) AS cogs_cents "
                    "FROM sales WHERE period = ?", (request.period,),
                )
            return sql_query(
                "SELECT revenue_cents, cogs_cents FROM sales WHERE period = ?",
                (request.period,),
            )

        return [Operation(self.request_type, self.result_type, execute, many=self.aggregation == "rows")]

    def bind(self, sql_query, calls, events):
        return bind_operations(self.operations(sql_query), calls, events,
                               request_error="query expects a validated SalesQuery instance")


def policy_for(task, mode="totals") -> QueryPolicy:
    """Compile trusted task context into Python literal types, once at startup."""
    if mode not in ("totals", "rows"):
        raise ValueError("Policy must be totals or rows")
    periods = ((task.period, task.previous_period) if task.include_previous_period
               else (task.period,))
    aggregation = "sum" if mode == "totals" else "rows"
    request_type = create_model(
        "SalesQuery", __base__=PolicyModel,
        period=(Literal[periods], ...),
        aggregation=(Literal[aggregation], ...),
    )
    return QueryPolicy(request_type, SalesTotals if mode == "totals" else Sale)


# Handwritten reference workflows; never supplied to the agent.
REFERENCE_WORKFLOWS = {
    "totals": {
        "revenue": 't = query(SalesQuery(period=period, aggregation="sum"))\nt.revenue_cents / 100',
        "margin": 't = query(SalesQuery(period=period, aggregation="sum"))\n'
                  '(t.revenue_cents - t.cogs_cents) / t.revenue_cents',
        "change": '''\
current = query(SalesQuery(period=period, aggregation="sum"))
previous = query(SalesQuery(period=previous_period, aggregation="sum"))
current_margin = (current.revenue_cents - current.cogs_cents) / current.revenue_cents
previous_margin = (previous.revenue_cents - previous.cogs_cents) / previous.revenue_cents
(current_margin - previous_margin) * 100
''',
    },
    "rows": {
        "revenue": 'rows = query(SalesQuery(period=period, aggregation="rows"))\n'
                   'sum(row.revenue_cents for row in rows) / 100',
        "margin": '''\
rows = query(SalesQuery(period=period, aggregation="rows"))
revenue = sum(row.revenue_cents for row in rows)
cogs = sum(row.cogs_cents for row in rows)
(revenue - cogs) / revenue
''',
        "change": '''\
margins = []
for month in (period, previous_period):
    rows = query(SalesQuery(period=month, aggregation="rows"))
    revenue = sum(row.revenue_cents for row in rows)
    cogs = sum(row.cogs_cents for row in rows)
    margins.append((revenue - cogs) / revenue)
(margins[0] - margins[1]) * 100
''',
    },
}

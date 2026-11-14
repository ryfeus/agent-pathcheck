"""A complete evidence-checked domain with no database, MCP, or model API."""

from contextlib import contextmanager
from pydantic import model_validator
from agent_pathcheck import (
    Domain, Invocation, Operation, PolicyModel, PolicyViolation,
    bind_operations, current_evidence, validate_model_output,
)


class Read(PolicyModel):
    pass


class Number(PolicyModel):
    value: int


class Total(PolicyModel):
    value: int

    @model_validator(mode="after")
    def check_total(self):
        rows = current_evidence().require(Read())
        if self.value != sum(row.value for row in rows):
            raise PolicyViolation("arithmetic", "total.sum", "Total must match observed numbers")
        return self


def make_domain(values=(2, 4)):
    @contextmanager
    def open_invocation(report):
        query = bind_operations([
            Operation(Read, Number, lambda request: [{"value": value} for value in values]),
        ], report["policy_calls"], report["policy_events"])
        yield Invocation(
            inputs={}, functions={"query": query}, constructors={"Read": Read, "Total": Total},
            validate_result=lambda value: validate_model_output(value, Total, report["policy_events"]),
        )
    return Domain(context={"unit": "items"}, open_invocation=open_invocation)


WORKFLOW = "rows = query(Read())\nTotal(value=sum(row.value for row in rows))"

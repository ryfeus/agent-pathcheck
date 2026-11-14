"""Expose a Pydantic-governed Monty workflow as a local stdio MCP tool."""

import argparse
import json
from pathlib import Path
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mcp.server import MCPServer
from agent_pathcheck.integrations.mcp import create_code_server
from agent_pathcheck.rendering import Input, render_contract, tool_description
from agent_pathcheck.adapters.sqlite import adapter_limits
from typing import Literal

if __package__:
    from .policy import policy_for
    from .tracer import (
        BASE_ROWS, BASE_TASKS, MAX_SOURCE_CHARS, VARIANT_ROWS, VARIANT_TASKS,
        Task, run_checked,
    )
else:
    from policy import policy_for
    from tracer import (
        BASE_ROWS, BASE_TASKS, MAX_SOURCE_CHARS, VARIANT_ROWS, VARIANT_TASKS,
        Task, run_checked,
    )


def create_server(task: Task, *, rows=BASE_ROWS, policy="totals") -> MCPServer:
    """Bind trusted task, fixture and policy before registering the code-only tool."""
    fixture = tuple(rows)
    contract = policy_for(task, policy)
    guest_inputs = {
        "question": task.question,
        "period": task.period,
        "previous_period": task.previous_period,
    }
    source = render_contract(contract.operations(None), {"SalesQuery": contract.request_type},
                             {name: Input(Literal[value], value) for name, value in guest_inputs.items()})
    description = tool_description(
        f"Execute a Python sales workflow. Question: {task.question}\n"
        "Revenue and cogs are cents. Gross margin is (revenue - cogs) / revenue; "
        "margin change is (current margin - previous margin) * 100 percentage points. "
        "The final expression is a finite number, checked against a host-owned fixture answer. "
        "At least one successful typed query is required.", source, adapter_limits=adapter_limits(),
    )
    return create_code_server("sql-math-tracer", description,
                              lambda code: run_checked(task, code, rows=fixture, policy=contract),
                              tool_name="run_workflow")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=tuple(BASE_TASKS), default="margin")
    parser.add_argument("--fixture", choices=("base", "variant"), default="base")
    parser.add_argument("--policy", choices=("totals", "rows"), default="totals",
                        help="Host-owned Python query contract (default: totals)")
    args = parser.parse_args()
    tasks, rows = (
        (BASE_TASKS, BASE_ROWS) if args.fixture == "base"
        else (VARIANT_TASKS, VARIANT_ROWS)
    )
    create_server(tasks[args.case], rows=rows, policy=args.policy).run(transport="stdio")


if __name__ == "__main__":
    main()

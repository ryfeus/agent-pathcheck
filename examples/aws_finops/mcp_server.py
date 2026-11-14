"""SQLite FinOps reference: local demo, stdio MCP server, or an optional model loop."""

import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile

# The agent launches this fixed script directly from a temporary working directory.
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mcp import Client
from openai import APIError

from agent_pathcheck.integrations.mcp import create_code_server
from agent_pathcheck.rendering import tool_description
from agent_pathcheck.semantic import SemanticReader
from agent_pathcheck.adapters.sqlite import adapter_limits

from examples.agent_support import DEFAULT_ENV_FILE, api_client, api_error, require_model, run_loop, server_parameters
from examples.aws_finops.policy import build_policy
from examples.aws_finops.tracer import COST_WORKFLOW, LIMITS, QUESTIONS, replays_for, rightsizing_workflow, run_checked
from examples.aws_finops.claims import QUESTION_TEXT
from examples.aws_finops.semantic import directory_for


SERVER = Path(__file__).resolve()


def create_server(*, case="rightsizing", fixture="base", policy="memory-required",
                  fixture_sql=None, expected_result=None, limits=None, replays=False, question=None):
    bundle = build_policy(case, policy, question=question)
    question = bundle.parameters.get('question')
    semantic = SemanticReader(directory_for(case, policy, question))
    effective_limits = {**LIMITS, **(limits or {})}
    claim_guidance = ('Construct at most one final claim per invocation; proposal constructors are eligibility filters. '
                      'Repair a rejected final claim by submitting a new workflow. ')
    budgets = {'max_claim_attempts': 1}
    if case == 'cost-claims':
        claim_guidance = ('Compose answer claims from completed query requests. Omit value or rows to let the host fill them. '
                          'A rejected claim or Answer permanently fails this invocation; submit a new workflow to repair it. '
                          'Answer text allows only simple placeholders naming claims and no literal digits. ')
        budgets = {'max_claim_rejections': 0}
    description = tool_description(
        "Execute a Python FinOps analysis/proposal workflow over synthetic AWS-style data. "
        "All amounts are USD cents. Proposals do not change AWS.\n"
        f"Question: {QUESTION_TEXT[question] if case == 'cost-claims' else QUESTIONS[case]}\nActive policy: {policy}\n"
        "The source below includes host validators; current_policy and current_evidence are host-only. "
        "Query evidence, compute the result, and return the output model. "
        + claim_guidance +
        "An unresolved block on an exact request declared necessary by the task policy can justify Abstain; "
        "use the reported kind and rule_id. Blocks on unrelated requests cannot justify abstention.",
        bundle.contract, limits={**effective_limits, **budgets}, adapter_limits=adapter_limits(),
    )
    index = semantic.read("index")
    if len(index) > 3000:
        raise ValueError("Semantic index exceeds its description budget")
    description += "\n\n" + index
    return create_code_server(
        "aws-finops-reference", description,
        lambda code: run_checked(code, case=case, fixture=fixture, policy=policy,
                                 fixture_sql=fixture_sql, expected_result=expected_result, limits=effective_limits,
                                 semantic_hash=semantic.docs_hash, question=question,
                                 replays=replays_for(case) if replays else (), require_replays=replays),
        tool_name="run_workflow", read_semantic=semantic.read,
    )


async def run_agent(*, model, case="rightsizing", fixture="base", policy="memory-required",
                    env_file=DEFAULT_ENV_FILE, question=None):
    model = require_model(model)
    with tempfile.TemporaryDirectory(prefix="finops-reference-") as directory:
        parameters = server_parameters(Path(directory), case, fixture, policy, server=SERVER)
        if question is not None:
            parameters.args += ['--question', question]
        async with api_client(env_file) as api, Client(parameters, read_timeout_seconds=45) as mcp:
            report = await run_loop(api, mcp, model=model)
    return {"model": model, "case": case, "fixture": fixture, "policy": policy, **report}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=tuple(QUESTIONS), default="rightsizing")
    parser.add_argument("--fixture", choices=("base", "variant"), default="base")
    parser.add_argument('--question', choices=tuple(QUESTION_TEXT), help='Select a cost-claims question')
    parser.add_argument("--policy", choices=("cpu-only", "memory-required", "costs-with-caveats", "memory-required-min-savings"), default="memory-required")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--demo", action="store_true", help="Execute a handwritten reference workflow")
    mode.add_argument("--agent", action="store_true", help="Let the selected model propose and repair the workflow")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--model", type=require_model, help="Required with --agent")
    args = parser.parse_args()
    if args.agent and args.model is None:
        parser.error("--agent requires --model")
    if args.demo and args.policy == "memory-required-min-savings":
        parser.error("The held-out mode is available through explicit benchmark scenarios, not demo prompts")
    context = dict(case=args.case, fixture=args.fixture, policy=args.policy)
    if args.question is not None:
        context['question'] = args.question
    if not args.demo and not args.agent:
        create_server(**context).run(transport="stdio")
        return
    if args.demo:
        code = COST_WORKFLOW if args.case == "costs" else rightsizing_workflow(args.policy)
        if args.policy == "costs-with-caveats":
            from examples.aws_finops.semantic_cases import cost_witness
            code = cost_witness()
        if args.case == 'cost-claims':
            from examples.aws_finops.claims_cases import claim_witness
            code = claim_witness(args.question or 'comparison')
        report = run_checked(code, **context)
        result = {**context, "code": code, **report}
        passed = report["validation_status"] == "passed"
    else:
        try:
            result = asyncio.run(run_agent(**context, model=args.model, env_file=args.env_file))
        except APIError as exc:
            result = {"status": "api_error", **api_error(exc)}
        passed = (result.get("status") == "completed"
                  and result.get("validation_status") == "passed")
    print(json.dumps(result, indent=2, allow_nan=False))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

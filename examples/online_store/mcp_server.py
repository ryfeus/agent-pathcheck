"""Offline store demos, a frozen-policy MCP server, and an optional model loop."""

import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mcp import Client
from agent_pathcheck.integrations.mcp import create_code_server
from agent_pathcheck.presentation import validated_fields
from agent_pathcheck.rendering import tool_description
from agent_pathcheck.adapters.sqlite import adapter_limits
from examples.agent_support import DEFAULT_ENV_FILE, api_client, require_model, run_loop, server_parameters
from examples.online_store.concepts import StoreConfig
from examples.online_store.counterfactuals import replays_for
from examples.online_store.planner import DEFAULT_PLAN, plan_question, validate_plan
from examples.online_store.policy import CASES, build_policy
from examples.online_store.semantic import snapshot
from examples.online_store.tracer import LIMITS, question_for, run_checked
from examples.online_store.workflows import workflow


SERVER = Path(__file__).resolve()


def execution_guidance(bundle, *, require_replays=False):
    """Describe the actual guest boundary, separately from host validator source."""
    constructors = ", ".join(bundle.parameters["models"])
    output = bundle.output_model.__name__
    guidance = (
        f"Guest interface: query and these supplied constructors are callable: {constructors}. "
        "The other function definitions and model methods shown in the contract are HOST implementation, not guest bindings. "
        "Do not call current_policy, current_evidence, observed, measure_result, eligible_stock, customer_selection, "
        "refund_decision, or other host helpers. Do not import libraries or copy host validators into your workflow. "
        "Use the supplied inputs and returned row attributes with ordinary Python loops, comprehensions and arithmetic. "
        f"The last expression must be a {output} instance, or a justified Abstain instance. "
        "If you assign the result to a variable, put that variable alone on the final line. "
        "Do not call .checked(), .model_dump(), or a model validator: the host validates the final instance automatically. "
        "Each run_workflow call starts fresh. Repeat all required query calls and construct the final answer in ONE submitted program. "
        "Constructing a request is not a read; call query(request) before dependent claims. "
        "Catch ValueError for query or constructor rejections, not the host-only PolicyViolation exception. "
        "For justified refusal, catch the required query's ValueError in the same program and then finish with "
        "Abstain(kind=..., rule_id=..., detail=...) using the reported block kind and rule ID. "
        "A blocked query from an earlier workflow submission cannot support a new abstention. "
    )
    case = bundle.parameters["case"]
    if case in ("loyalty", "churn"):
        guidance += (
            "Use the supplied customer_windows UTC boundaries; no date imports or host date helpers are needed. "
            "Test window['start'] <= row.last_order_at < window['end'] after excluding None. "
            "For loyalty, use each cutoff's window and store_config['gold_cents']; for churn, use customer_windows['churn']. "
        )
    if case == "restock":
        guidance += (
            "RestockProposal is an eligibility filter: catch its ValueError and skip that candidate. "
            "Sort and select from this invocation's observed rows, then compute the selected forecast total. "
        )
    if case == "promotion-review":
        guidance += (
            "Query the two MeasureQuery requests and PromotionQuery before constructing the required claims. "
            "Pass the request instances as claim sources. Omit rows to let the host fill checked claim values. "
        )
    if require_replays:
        guidance += (
            "Required counterfactual replays execute the SAME code against changed data. Recompute values and selections "
            "from each invocation's rows; do not hardcode observed amounts or identifiers. A passed base contract or "
            "fixture grade cannot override failed replay. Repair until validation_status is passed. "
        )
    return guidance


def create_server(case="restock", policy="base", **options):
    bundle = build_policy(case, policy, **{key: options[key] for key in ("config", "plan", "refund") if key in options})
    semantic = snapshot(bundle)
    prose = ("Execute a read-only online-store workflow over synthetic SQLite records. Refund and restock outputs are proposals only. "
             "All money is integer USD cents. Only committed evidence supports a checked answer. "
             "An exact unresolved task-required block can justify Abstain. Model narrative and causal explanations are unverified.\n"
             + question_for(bundle) + "\n")
    composed = case == "promotion-review"
    prose += ("Compose the required claims; a rejected claim permanently fails this invocation. " if composed else
              "Construct one final claim per invocation; RestockProposal constructors are eligibility filters. ")
    prose += execution_guidance(bundle, require_replays=options.get("require_replays",False))
    description = tool_description(prose, bundle.contract, limits={**LIMITS,
        "max_claim_attempts": None if composed else 1, "max_claim_rejections": 0 if composed else None},
        adapter_limits=adapter_limits()) + "\n\n" + semantic.read("index")
    return create_code_server("online-store-reference", description,
        lambda code: run_checked(code, case=case, policy=policy, semantic_hash=semantic.docs_hash, **options),
        read_semantic=semantic.read)


async def run_agent(*, model, case="restock", policy="base", fixture_name="base", config=None,
                    plan=None, question=None, env_file=DEFAULT_ENV_FILE, replays=False):
    model = require_model(model)
    config = config or StoreConfig()
    planning = None
    async with api_client(env_file) as api:
        if case == "composed" and question:
            planning = await plan_question(api, question, model=model, config=config)
            if planning["status"] != "planned":
                return {"model": model, **planning, **validated_fields(None)}
            plan = planning["plan"]
        if case == "composed":
            plan = validate_plan(plan or DEFAULT_PLAN, config).model_dump()
        with tempfile.TemporaryDirectory(prefix="online-store-agent-") as directory:
            root = Path(directory)
            parameters = server_parameters(root, case, fixture_name, policy, server=SERVER)
            if replays:
                parameters.args.append("--replays")
            config_file = root / "config.json"
            config_file.write_text(config.model_dump_json())
            parameters.args += ["--config-file", str(config_file)]
            if plan:
                plan_file = root / "plan.json"
                plan_file.write_text(json.dumps(plan))
                parameters.args += ["--plan-file", str(plan_file)]
            async with Client(parameters, read_timeout_seconds=45) as mcp:
                result = await run_loop(api, mcp, model=model, additional_instructions=(
                    "Distinguish the compiled plan's interpretation from its checked execution. "
                    "Report policy validation separately from unverified narrative. "
                    "Follow the Guest interface section of run_workflow: host helper functions and model methods "
                    "are not guest bindings. End each program with the output instance itself. "
                    "If a required read is blocked, catch ValueError in the same program and return a typed Abstain instance. "
                    "If validation_status has not passed and budget remains, repair the workflow instead of ending with prose."))
        return {"model": model, "case": case, "planning": planning, **result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=CASES, default="restock")
    parser.add_argument("--policy", choices=("base", "minimum-monthly-sales"), default="base")
    parser.add_argument("--fixture", choices=("base", "truncated-returns"), default="base")
    parser.add_argument("--active-days", type=int, choices=(60, 90), default=None)
    parser.add_argument("--config-file", type=Path)
    parser.add_argument("--plan-file", type=Path)
    parser.add_argument("--question", help="Natural-language planning question for --agent --case composed")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--replays", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--demo", action="store_true")
    mode.add_argument("--agent", action="store_true")
    mode.add_argument("--all", action="store_true", help="Run offline acceptance for all ten levels")
    parser.add_argument("--model", type=require_model, help="Required with --agent")
    args = parser.parse_args()
    if args.agent and args.model is None:
        parser.error("--agent requires --model")
    if args.question and not (args.agent and args.case == "composed"):
        parser.error("--question requires --agent --case composed")
    if args.plan_file and args.case != "composed":
        parser.error("--plan-file requires --case composed")
    if args.all:
        from examples.online_store.acceptance import acceptance
        result = acceptance(replays=args.replays)
        print(json.dumps(result, indent=2))
        raise SystemExit(not result["passed"])
    config_data = json.loads(args.config_file.read_text()) if args.config_file else {}
    if args.active_days is not None:
        config_data["active_days"] = args.active_days
    config = StoreConfig(**config_data)
    plan = json.loads(args.plan_file.read_text()) if args.plan_file else None
    options = dict(case=args.case, policy=args.policy, fixture_name=args.fixture, config=config)
    if plan is not None:
        options["plan"] = plan
    if args.demo:
        code = workflow(args.case, args.policy, config=config, plan=plan)
        trials = replays_for(args.case) if args.replays else ()
        result = run_checked(code, **options, replays=trials, require_replays=bool(trials))
        result["code"] = code
        passed = result["validation_status"] == "passed" and result["answer_check"] != "failed"
    elif args.agent:
        result = asyncio.run(run_agent(**options, model=args.model, question=args.question, env_file=args.env_file, replays=args.replays))
        passed = result.get("status") == "completed" and result.get("validation_status") == "passed"
    else:
        trials = replays_for(args.case) if args.replays else ()
        create_server(**options, replays=trials, require_replays=bool(trials)).run(transport="stdio")
        return
    print(json.dumps(result, indent=2, allow_nan=False))
    raise SystemExit(not passed)


if __name__ == "__main__":
    main()

"""Opt-in real-model evaluation; expected answers stay outside model context."""

import argparse
import asyncio
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import time

from examples.agent_support import require_model
from .concepts import StoreConfig
from .mcp_server import run_agent
from .planner import DEFAULT_PLAN, PlanSpec
from .policy import CASES
from .tracer import EXPECTED, projection


def scenarios():
    cases = {case: dict(case=case, expected=EXPECTED[case]) for case in CASES}
    cases.update({
        "restock-corrected": dict(case="restock", policy="minimum-monthly-sales",
            expected=(1295000,["P-101","P-102","P-107","P-108","P-110"])),
        "returns-blocked": dict(case="return-rate",fixture_name="truncated-returns",expected="limit.max_rows"),
        "loyalty-active-60": dict(case="loyalty",active_days=60,expected={"customer_ids":["C-future","C-new"]}),
        "churn-active-60": dict(case="churn",active_days=60,expected={"customer_ids":["C-churn","C-window","C-not-gold"]}),
        "planner-suppliers": dict(case="composed", question="Which five suppliers had the most late deliveries to Gold customers last quarter?",
            expected_plan=DEFAULT_PLAN, expected=EXPECTED["composed"]),
        "planner-comparison": dict(case="composed", question="Compare production net revenue by category from September 2026 to October 2026. Include every category.",
            expected_plan=dict(measure="net_revenue",group_by="category",periods=["2026-09","2026-10"],shape="comparison"),
            expected=EXPECTED["category-comparison"]),
        "planner-customers": dict(case="composed", question="Which two West-region customers generated the most production net revenue in October 2026?",
            expected_plan=dict(measure="net_revenue",group_by="customer",periods=["2026-10"],shape="top_k",top=2,region="West"),
            expected=(2000000,[("C-west",2000000)])),
    })
    return cases


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"))


def evaluate(spec, result):
    """Grade completed validation, fixture answer, and planned intent separately."""
    validation = result.get("status") == "completed" and result.get("validation_status") == "passed"
    answer = False
    if validation:
        answer = canonical(projection(spec["case"],result["validated_result"])) == canonical(spec["expected"])
    planning = result.get("planning") or (result if result.get("status") == "planning_failed" else None)
    intent = None
    if "expected_plan" in spec:
        intent = bool(planning and planning.get("status") == "planned"
                      and planning.get("plan") == PlanSpec(**spec["expected_plan"]).model_dump())
    calls = result.get("tool_calls",[])
    first = calls[0].get("output",{}).get("validation_status") == "passed" if calls else False
    usage = [*result.get("model_usage",[]), *((planning or {}).get("model_usage",[]) if planning is not result else [])]
    return dict(passed=validation and answer and intent is not False, status=result.get("status"),
                validation_status=result.get("validation_status"), answer_matches=answer,
                plan_matches=intent, first_workflow_passed=first, workflow_calls=len(calls),
                planning_attempts=len((planning or {}).get("attempts",[])),
                lookup_calls=len(result.get("lookups",[])),
                input_tokens=sum((item or {}).get("input_tokens",0) for item in usage),
                output_tokens=sum((item or {}).get("output_tokens",0) for item in usage),
                replay_check=(result.get("last_report") or {}).get("replay_check"),
                failure=(result.get("last_report") or {}).get("failure") or result.get("failure") or result.get("error"))


def source_hash():
    root = Path(__file__).resolve().parents[2]
    files = [*Path(__file__).parent.glob("*.py"), *(root/"agent_pathcheck").rglob("*.py"),
             root/"examples/agent_support.py", root/"examples/sql_math_tracer/requirements.txt"]
    return sha256("".join(str(path.relative_to(root)) + "\n" + path.read_text() for path in sorted(files)).encode()).hexdigest()


async def sweep(names, output, *, model, concurrency=2):
    model = require_model(model)
    output.mkdir(parents=True,exist_ok=True)
    matrix = scenarios()
    semaphore = asyncio.Semaphore(concurrency)
    summary = dict(model=model, started_at=datetime.now(timezone.utc).isoformat(), source_sha256=source_hash(),
                   replays_required_where_available=True, scenarios={})

    async def trial(name):
        spec = matrix[name]
        path = output/(name+".json")
        if path.exists():
            raise ValueError(f"Refusing to overwrite retained trial {path}")
        options = {key:value for key,value in spec.items() if key not in ("expected","expected_plan","active_days")}
        options["config"] = StoreConfig(active_days=spec.get("active_days",90))
        options["replays"] = True
        async with semaphore:
            start = time.monotonic()
            try:
                result = await run_agent(**options, model=model)
            except Exception as exc:
                # Exceptions may embed API request details; retain their type only.
                result = dict(status="infrastructure_error",error=type(exc).__name__,validation_status="not_run")
            elapsed = round(time.monotonic()-start,3)
            raw = json.dumps(result,indent=2,allow_nan=False) + "\n"
            path.write_text(raw)
            outcome = {**evaluate(spec,result),"seconds":elapsed,"artifact":path.name,"sha256":sha256(raw.encode()).hexdigest()}
            summary["scenarios"][name] = outcome
            (output/"summary.json").write_text(json.dumps(summary,indent=2)+"\n")
            print(json.dumps({"scenario":name,**outcome}),flush=True)

    await asyncio.gather(*(trial(name) for name in names))
    summary.update(passed=all(row["passed"] for row in summary["scenarios"].values()),
                   finished_at=datetime.now(timezone.utc).isoformat())
    (output/"summary.json").write_text(json.dumps(summary,indent=2)+"\n")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario",choices=tuple(scenarios()),action="append")
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--concurrency",type=int,choices=(1,2,3),default=2)
    parser.add_argument("--model", type=require_model, required=True, help="Model ID available to your API key")
    args = parser.parse_args()
    result = asyncio.run(sweep(args.scenario or list(scenarios()),args.output,model=args.model,concurrency=args.concurrency))
    raise SystemExit(not result["passed"])


if __name__ == "__main__":
    main()

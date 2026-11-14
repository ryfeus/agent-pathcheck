"""Offline contract benchmark and bounded opt-in model trials through isolated stdio MCP."""

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mcp import Client

from examples.aws_finops.benchmark_cases import negative_probes, scenarios
from examples.aws_finops.semantic_cases import semantic_scenarios, semantic_witness
from examples.aws_finops.claims_cases import claims_scenarios, claim_witness, negative_probes as claim_probes
from examples.aws_finops.lookup_benchmark import (
    LookupScenario, grade_lookup, lookup_instructions, lookup_preflight, lookup_scenarios,
)
from examples.aws_finops.mcp_server import create_server
from examples.aws_finops.tracer import replays_for, run_checked
from agent_pathcheck.extensions.engine import Engine
from examples.agent_support import (
    DEFAULT_ENV_FILE, MAX_TOOL_CALLS, MAX_TURNS, MAX_LOOKUPS, api_client, require_model, run_loop, server_parameters,
)


ROOT = Path(__file__).resolve().parents[2]
HELD_OUT_MODES = ["memory-required-min-savings"]


def witness(scenario):
    return claim_witness(scenario.question) if scenario.case == 'cost-claims' else semantic_witness(scenario)


def comparable_result(scenario, result):
    if scenario.case == 'cost-claims' and result is not None and 'claims' in result:
        from examples.aws_finops.claims import projection
        return projection(result, scenario.question)
    return result


def now():
    return datetime.now(timezone.utc).isoformat()


def source_identity():
    # Record dirty source content as well as HEAD so results remain attributable
    # before the work is committed. Never inspect credential files.
    files = sorted((ROOT / "examples/aws_finops").glob("*.py"))
    files += sorted((ROOT / "examples/sql_math_tracer").glob("*.py"))
    files += sorted((ROOT / "agent_pathcheck").rglob("*.py"))
    files.append(ROOT / "examples/agent_support.py")
    return {
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
    }


def offline_benchmark(cases, *, replays=False):
    started = time.perf_counter()
    with Engine() as engine:
        result = _offline_benchmark(cases, engine=engine, replays=replays)
    result["seconds"] = time.perf_counter() - started
    return result


def _offline_benchmark(cases, *, engine, replays):
    rows = []
    for scenario in cases.values():
        code = witness(scenario)
        report = run_checked(code, **scenario.host_options(), engine=engine,
                             replays=replays_for(scenario.case) if replays and not scenario.blocked_by else (),
                             require_replays=replays and not scenario.blocked_by)
        if scenario.blocked_by:
            matched = report["validated_answer"] is None and scenario.blocked_by in str(report["errors"])
        else:
            matched = (report["validation_status"] == "passed" and report["answer_check"] == "passed"
                       and comparable_result(scenario, report['result']) == scenario.expected)
        rows.append(dict(name=scenario.name, kind="blocked_input" if scenario.blocked_by else "solvable",
                         challenge=scenario.challenge, expected=scenario.expected,
                         matched=matched, code=code, report=report))
    probes = negative_probes() + [(name, 'claims_' + scenario, code, reason)
                                 for name, scenario, code, reason in claim_probes()]
    for name, scenario_name, code, reason in probes:
        if scenario_name not in cases:
            continue
        report = run_checked(code, **cases[scenario_name].host_options(), engine=engine)
        rejected = reason in str(report['errors'])
        if cases[scenario_name].case == 'cost-claims' and reason != 'validated Answer':
            rejected = (reason in [error['rule_id'] for event in report['policy_events']
                                  for error in event.get('errors', [])]
                        and (report['failure'] or {}).get('rule_id') == 'claims.rejection_limit')
        rows.append(dict(name=name, scenario=scenario_name, kind="deliberate_bad_program", code=code,
                         matched=report["validated_answer"] is None and rejected,
                         expected_error=reason, report=report))
    audit = audit_metrics([dict(scenario=row.get("scenario", row["name"]), reports=[row["report"]],
                               last_report=row["report"], lookups=0, trial=row["kind"] != "deliberate_bad_program")
                           for row in rows])
    return {"checks": len(rows), "matched": sum(row["matched"] for row in rows), "rows": rows, "summary": audit}


def grade_trial(scenario, result):
    """Keep failed submissions, successful repairs, and impossible inputs distinct."""
    attempts = [call["output"] for call in result.get("tool_calls", [])]
    valid = [report.get("validation_status") == "passed" for report in attempts]
    if result.get("status") in ("api_error", "mcp_error"):
        outcome = "infrastructure_error"
    elif (scenario.blocked_by and result.get("status") == "completed"
          and result.get("validation_status") == "passed"
          and (result.get("validated_result") or {}).get("kind") == "abstained"):
        outcome = "justified_abstention"
    elif scenario.blocked_by:
        if any(valid) or result.get("validated_answer") is not None:
            outcome = "unexpected_acceptance"
        elif any(scenario.blocked_by in str(report.get("errors", [])) for report in attempts):
            outcome = "expected_block"
        else:
            outcome = "unverified_block"
    elif result.get("validation_status") == "passed" and comparable_result(scenario, result.get("validated_result")) != scenario.expected:
        outcome = "incorrect_acceptance"
    elif (result.get("status") == "completed" and result.get("validation_status") == "passed"
          and comparable_result(scenario, result.get("validated_result")) == scenario.expected):
        outcome = "solved"
    else:
        outcome = "agent_failed"
    grade = dict(outcome=outcome, first_attempt_passed=bool(valid and valid[0]),
                 failed_submissions=sum(not value for value in valid), tool_calls=len(attempts),
                 repaired=outcome == "solved" and bool(valid) and not valid[0])
    if isinstance(scenario, LookupScenario):
        grade['workflow_outcome'] = outcome
        grade['semantic_lookup'] = grade_lookup(scenario, result)
        if outcome == 'solved' and not grade['semantic_lookup']['passed']:
            grade['outcome'] = 'semantic_lookup_failed'
    return grade


def audit_metrics(trials):
    rejected = Counter()
    violations = Counter()
    inconclusive = 0
    for trial in trials:
        for report in trial['reports']:
            rejected[trial['scenario']] += sum(report.get('claim_attempts_rejected', {}).values())
            inconclusive += sum(r.get('verdict') == 'inconclusive' for r in report.get('replays', []))
            violations.update(e['kind'] for event in report.get('policy_events', [])
                              for e in event.get('errors', []) if 'kind' in e)
    counted = [t for t in trials if t.get('trial', True)]
    abstentions = sum(((t.get('last_report') or {}).get('result') or {}).get('kind') == 'abstained'
                     and (t.get('last_report') or {}).get('contract_check') == 'passed' for t in counted)
    return dict(claim_attempts_rejected={'total':sum(rejected.values()), 'per_scenario':dict(rejected)},
                abstentions=abstentions, abstention_trials=len(counted),
                abstention_rate=abstentions/len(counted) if counted else 0,
                replay_inconclusive=inconclusive, docs_lookups_per_trial=[t.get('lookups', 0) for t in counted],
                violation_kinds=dict(violations), held_out_modes=list(HELD_OUT_MODES))


def summarize(trials):
    outcomes = Counter(row["grade"]["outcome"] for row in trials)
    solvable = [row for row in trials if row["blocked_by"] is None]
    usage = [u for row in trials for u in row["agent"].get("model_usage", [])]
    reports = [call["output"] for row in trials for call in row["agent"].get("tool_calls", [])]
    failures = Counter(report["failure"]["class"] for report in reports if report.get("failure"))
    replays = [trial for report in reports for trial in report.get("replays", [])]
    violations = Counter(error["kind"] for report in reports for event in report.get("policy_events", [])
                         for error in event.get("errors", []) if "kind" in error)
    audit = audit_metrics([dict(scenario=row.get('scenario', str(i)),
                               reports=[c['output'] for c in row['agent'].get('tool_calls', [])],
                               last_report=row['agent'].get('last_report'), lookups=len(row['agent'].get('lookups', [])))
                           for i,row in enumerate(trials)])
    lookup_trials = [row for row in trials if 'semantic_lookup' in row['grade']]
    return dict(**audit, failure_classes=dict(failures),
                semantic_lookup_trials=len(lookup_trials),
                semantic_lookup_passes=sum(row['grade']['semantic_lookup']['passed'] for row in lookup_trials),
                semantic_lookup_workflow_solves=sum(row['grade']['workflow_outcome'] == 'solved' for row in lookup_trials),
                replay_verdicts=dict(Counter(trial.get("verdict", "historical") for trial in replays)),
                replay_exemptions=sum(bool(trial.get("exemption_reason")) for trial in replays),
                seconds=sum(row.get("seconds", 0) for row in trials), trials=len(trials), outcomes=dict(outcomes), solvable_trials=len(solvable),
                first_attempt_passes=sum(row["grade"]["first_attempt_passed"] for row in solvable),
                repaired_trials=sum(row["grade"]["repaired"] for row in solvable),
                failed_submissions=sum(row["grade"]["failed_submissions"] for row in trials),
                input_tokens=sum(u.get("input_tokens", 0) for u in usage),
                output_tokens=sum(u.get("output_tokens", 0) for u in usage))


async def live_benchmark(cases, repeats, env_file, record, save, *, model):
    model = require_model(model)
    # Two independent processes avoid shared fixture state; each gets the same
    # production submission/lookup/turn limits. Lookup cases add an explicit
    # documentation task; ordinary scenarios keep their original instructions.
    semaphore = asyncio.Semaphore(2)
    async with api_client(env_file) as api:
        async def trial(scenario, repeat):
            async with semaphore:
                started = time.monotonic()
                with tempfile.TemporaryDirectory(prefix="finops-benchmark-") as directory:
                    params = server_parameters(Path(directory), scenario.case, "base", scenario.policy,
                                               server=Path(__file__).resolve())
                    params.args = ["-E", "-s", str(Path(__file__).resolve()), "--serve", scenario.name]
                    if record.get("counterfactual_replays"):
                        params.args.append("--replays")
                    try:
                        async with Client(params, read_timeout_seconds=45) as mcp:
                            result = await run_loop(api, mcp, model=model, additional_instructions=lookup_instructions(scenario))
                    except Exception as exc:
                        # Transport metadata only: never persist credential-bearing exception messages.
                        result = {"status": "mcp_error", "error": type(exc).__name__, "tool_calls": []}
                row = dict(scenario=scenario.name, repeat=repeat, challenge=scenario.challenge,
                           blocked_by=scenario.blocked_by, expected=scenario.expected,
                           dataset_sha256=hashlib.sha256(scenario.sql.encode()).hexdigest(),
                           seconds=round(time.monotonic()-started, 3), agent=result,
                           grade=grade_trial(scenario, result))
                if isinstance(scenario, LookupScenario):
                    row['lookup_instructions'] = lookup_instructions(scenario)
                record["trials"].append(row)
                record["summary"] = summarize(record["trials"])
                save()
                brief_grade = {key: value for key, value in row['grade'].items() if key != 'semantic_lookup'}
                if 'semantic_lookup' in row['grade']:
                    brief_grade['semantic_lookup_passed'] = row['grade']['semantic_lookup']['passed']
                    brief_grade['lookup_attempts'] = len(result.get('lookups', []))
                print(json.dumps({"scenario": scenario.name, "repeat": repeat, **brief_grade}), flush=True)
        await asyncio.gather(*(trial(scenario, repeat) for scenario in cases.values() for repeat in range(1, repeats+1)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replays", action="store_true", help="Require counterfactual replay for solvable offline and live cases")
    case_group = parser.add_mutually_exclusive_group()
    case_group.add_argument("--semantic-cases", action="store_true", help="Run only the opt-in caveat and held-out correction scenarios")
    case_group.add_argument("--lookup-cases", action="store_true", help="Require relevant semantic reads and exact rule metadata as well as a correct workflow")
    case_group.add_argument('--claims-cases', action='store_true', help='Run the separate composable answer claims suite')
    parser.add_argument("--live", action="store_true", help="Run the selected model after offline preflight; uses the API key")
    parser.add_argument("--only", nargs="+", help="Scenario names; default is the full dataset")
    parser.add_argument("--repeats", type=int, choices=range(1, 6), default=1)
    parser.add_argument("--output", type=Path, help="New JSON artifact path; existing files are never overwritten")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--serve", help=argparse.SUPPRESS)
    parser.add_argument("--model", type=require_model, help="Required with --live")
    args = parser.parse_args()
    if args.live and args.model is None:
        parser.error("--live requires --model")
    cases = (claims_scenarios() if args.claims_cases else lookup_scenarios() if args.lookup_cases
             else semantic_scenarios() if args.semantic_cases else scenarios())
    if args.serve:
        scenario = {**scenarios(), **semantic_scenarios(), **lookup_scenarios(), **claims_scenarios()}[args.serve]
        create_server(**scenario.host_options(), replays=args.replays and not scenario.blocked_by).run(transport="stdio")
        return
    if args.only:
        unknown = set(args.only) - cases.keys()
        if unknown:
            parser.error(f"Unknown scenarios: {sorted(unknown)}")
        cases = {name: cases[name] for name in args.only}
    if args.output and args.output.exists():
        parser.error("Output already exists; choose a new path to preserve past runs")
    if args.live and args.output is None:
        parser.error("--live requires --output so every trial is retained")
    record = dict(started_at=now(), counterfactual_replays=args.replays, mode="live" if args.live else "offline", model=args.model if args.live else None,
                  suite='claims' if args.claims_cases else "lookup" if args.lookup_cases else "semantic" if args.semantic_cases else "historical",
                  model_config={"reasoning_effort": "low", "max_output_tokens": 2500,
                                "max_turns": MAX_TURNS, "max_tool_calls": MAX_TOOL_CALLS, "max_lookups": MAX_LOOKUPS},
                  **source_identity(), offline=offline_benchmark(cases, replays=args.replays), trials=[])
    if args.lookup_cases:
        record['lookup_preflight'] = asyncio.run(lookup_preflight(cases))

    def save():
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            # Each completed trial replaces only this newly-created run artifact.
            temp = args.output.with_suffix(args.output.suffix + ".tmp")
            temp.write_text(json.dumps(record, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
            temp.replace(args.output)

    save()
    print(json.dumps({"offline_checks": record["offline"]["checks"], "matched": record["offline"]["matched"]}), flush=True)
    if record["offline"]["checks"] != record["offline"]["matched"]:
        raise SystemExit("Offline expectations failed; live trials were not started")
    if args.lookup_cases:
        preflight = record['lookup_preflight']
        print(json.dumps({'lookup_preflight_checks': preflight['checks'], 'matched': preflight['matched']}), flush=True)
        if preflight['checks'] != preflight['matched']:
            raise SystemExit('Semantic lookup preflight failed; live trials were not started')
    if args.live:
        asyncio.run(live_benchmark(cases, args.repeats, args.env_file, record, save, model=args.model))
    record["completed_at"] = now()
    save()
    if args.live:
        print(json.dumps(record["summary"], indent=2))
    # An eval run completes successfully even when it discovers model failures.
    # Offline harness regressions fail above; trial verdicts remain in the artifact.


if __name__ == "__main__":
    main()

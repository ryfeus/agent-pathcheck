"""Replay retained model submissions offline, with typed timelines and timing."""

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter

from agent_pathcheck import ExecutionReport
from agent_pathcheck.extensions.engine import Engine
from .benchmark import source_identity
from .benchmark_cases import scenarios
from .tracer import run_checked


FIELDS = ("execution", "contract_check", "answer_check", "validation_status", "result")


def verify_artifact(path, manifest, *, content=None):
    entry = manifest["assets"].get(path.name)
    if entry is None:
        raise ValueError(f"Unknown artifact {path.name}; supply its retained --manifest")
    actual = sha256(path.read_bytes() if content is None else content).hexdigest()
    if actual != entry["sha256"]:
        raise ValueError(f"SHA-256 mismatch for {path.name}")
    return actual


def replay_artifacts(paths, *, manifest_path):
    manifest = json.loads(manifest_path.read_text())
    # Verify every input before parsing any submitted program or running Monty.
    contents = [(path, path.read_bytes()) for path in paths]
    input_hashes = {str(path): verify_artifact(path, manifest, content=data) for path, data in contents}
    cases, checks = scenarios(), []
    started = perf_counter()
    with Engine() as engine:
        for path, data in contents:
            artifact = json.loads(data)
            for trial in artifact["trials"]:
                for number, call in enumerate(trial["agent"].get("tool_calls", []), 1):
                    if "code" not in call.get("arguments", {}):
                        continue
                    code = call["arguments"]["code"]
                    report = run_checked(code, **cases[trial["scenario"]].host_options(), engine=engine, max_claim_attempts=None)
                    # Validate the entire trace after a JSON round trip.
                    ExecutionReport.model_validate_json(json.dumps(report, allow_nan=False))
                    checks.append(dict(source=str(path), scenario=trial["scenario"], submission=number,
                                       matched=all(report.get(key) == call["output"].get(key) for key in FIELDS),
                                       code=code, report=report))
    failures = Counter(row["report"]["failure"]["class"] for row in checks if row["report"]["failure"])
    return dict(mode="offline_saved_submission_replay", **source_identity(),
                input_sha256=input_hashes,
                compared_fields=FIELDS, seconds=perf_counter() - started,
                summary=dict(submissions=len(checks), matched=sum(row["matched"] for row in checks),
                             failure_classes=dict(failures)), checks=checks)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True,
                        help="Trusted SHA-256 manifest for the supplied artifacts")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; choose a new artifact path")
    try:
        result = replay_artifacts(args.artifacts, manifest_path=args.manifest)
    except (ValueError, KeyError, OSError) as exc:
        parser.error(str(exc))
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({**result["summary"], "seconds": result["seconds"]}))
    if result["summary"]["submissions"] != result["summary"]["matched"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

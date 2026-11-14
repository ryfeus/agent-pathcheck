"""Run with python -m examples.minimal from the repository root."""

import json
from agent_pathcheck import run
from agent_pathcheck.presentation import validated_fields
from .workflow import WORKFLOW, make_domain

execution = run(WORKFLOW, domain=make_domain())
print(json.dumps(validated_fields(execution.report), indent=2))
if execution.report["contract_check"] != "passed":
    raise SystemExit(1)

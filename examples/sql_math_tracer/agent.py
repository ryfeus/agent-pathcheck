"""API-key-only model loop grounded by an executable Pydantic/Monty policy."""

import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mcp import Client
from openai import APIError

from examples.agent_support import (
    DEFAULT_ENV_FILE, MAX_TURNS, MAX_TOOL_CALLS, INSTRUCTIONS,
    api_client, api_error, require_model, run_loop, server_parameters as _server_parameters,
)


SERVER = Path(__file__).with_name("mcp_server.py").resolve()


def server_parameters(directory, case, fixture, policy, *, server=SERVER):
    return _server_parameters(directory, case, fixture, policy, server=server)


async def run_agent(*, model, case="margin", fixture="base", policy="totals", env_file=DEFAULT_ENV_FILE):
    model = require_model(model)
    with tempfile.TemporaryDirectory(prefix="sales-agent-") as temp_dir:
        directory = Path(temp_dir)
        async with api_client(env_file) as api:
            async with Client(server_parameters(directory, case, fixture, policy),
                              read_timeout_seconds=45) as mcp:
                result = await run_loop(api, mcp, model=model)
    return {"model": model, "case": case, "fixture": fixture, "policy": policy, **result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("revenue", "margin", "change"), default="margin")
    parser.add_argument("--fixture", choices=("base", "variant"), default="base")
    parser.add_argument("--policy", choices=("totals", "rows"), default="totals")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--model", type=require_model, required=True, help="Model ID available to your API key")
    args = parser.parse_args()
    try:
        result = asyncio.run(run_agent(model=args.model, case=args.case, fixture=args.fixture,
                                      policy=args.policy, env_file=args.env_file))
    except APIError as exc:
        result = {"status": "api_error", **api_error(exc)}
    print(json.dumps(result, indent=2, allow_nan=False))
    if (result["status"] != "completed"
            or result.get("validation_status") != "passed"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

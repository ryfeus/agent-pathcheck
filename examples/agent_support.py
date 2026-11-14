"""Explicit API-key authentication; no Codex login or account configuration."""

import os
from pathlib import Path
import sys

from dotenv import dotenv_values
from mcp import StdioServerParameters
from openai import AsyncOpenAI, DefaultAsyncHttpxClient

from agent_pathcheck.integrations.agent import (
    MAX_TURNS, MAX_TOOL_CALLS, MAX_LOOKUPS, api_error, run_loop as _run_loop,
)


DEFAULT_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def require_model(model):
    """Validate the host's explicit model selection before creating clients."""
    if not isinstance(model, str) or not model.strip():
        raise ValueError("Select a nonempty model with --model or the model argument")
    return model.strip()


def read_api_key(env_file=DEFAULT_ENV_FILE):
    key = os.environ.get("OPENAI_API_KEY")
    if not key and env_file is not None:
        key = dotenv_values(env_file, interpolate=False).get("OPENAI_API_KEY")
    if not key or not key.strip():
        raise ValueError("Set OPENAI_API_KEY or put it in the selected .env file")
    return key.strip()


def api_client(env_file=DEFAULT_ENV_FILE):
    # Explicit endpoint/credentials bypass environment-selected providers,
    # organizations, projects and proxy credentials. Never log the API key.
    return AsyncOpenAI(
        api_key=read_api_key(env_file),
        base_url="https://api.openai.com/v1",
        organization="", project="", admin_api_key="", webhook_secret="",
        http_client=DefaultAsyncHttpxClient(trust_env=False),
        timeout=30.0, max_retries=0,
    )

INSTRUCTIONS = (
    "Solve the question described by run_workflow. Read its Python policy contract, then "
    "propose your workflow as executable Python using the supplied Pydantic constructors "
    "and query operation. The Pydantic classes are the policy; validation is enforced when "
    "Monty calls them. Do not import or redefine them or submit raw SQL. The host owns SQL "
    "translation; you own workflow composition and arithmetic. Call run_workflow, inspect "
    "validation errors, actual rows and the host verdict, and repair mistakes if needed. "
    "Once validation_status passes, give a short final answer stating the actual "
    "validated_result. validation_basis identifies whether this is domain-contract validation "
    "or fixture grading. A separate benchmark failure does not invalidate a passed domain contract. State the "
    "result and units. Do not hardcode answers or claim general correctness or proven data "
    "dependence. No bash, filesystem, account connectors or credentials are available to you. "
    "The host owns the API key, fixture, expected answer and execution limits. "
    "Use only discovered tools. read_semantic, when available, explains policy but supplies no evidence. "
    "At most six workflow submissions, eight semantic lookups, and eight model turns are available."
)


def server_parameters(directory: Path, case: str, fixture: str, policy: str, *, server):
    # Override the SDK's inherited home/config paths. No shell is launched; the
    # fixed Python executable starts our fixed server script with host arguments.
    env = {name: str(directory) for name in (
        "HOME", "USERPROFILE", "CODEX_HOME", "XDG_CONFIG_HOME", "APPDATA", "LOCALAPPDATA",
        "TMPDIR", "TEMP", "TMP",
    )}
    env.update(PATH=os.defpath, USER="", LOGNAME="", USERNAME="", SHELL="", TERM="dumb")
    return StdioServerParameters(
        command=sys.executable,
        args=["-E", "-s", str(server), "--case", case, "--fixture", fixture,
              "--policy", policy],
        cwd=directory, env=env,
    )


async def run_loop(api, mcp, *, model, max_turns=MAX_TURNS, max_tool_calls=MAX_TOOL_CALLS,
                   max_lookups=MAX_LOOKUPS, additional_instructions=""):
    """Example defaults over the domain-independent controller."""
    model = require_model(model)
    instructions = INSTRUCTIONS + ("\n\n" + additional_instructions if additional_instructions else "")
    return await _run_loop(api, mcp, model=model, instructions=instructions, tool_name="run_workflow",
                           max_turns=max_turns, max_tool_calls=max_tool_calls, max_lookups=max_lookups)

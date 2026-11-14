"""Offline loop tests: mocked model replies, real MCP and Monty execution."""

import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx2
from mcp import Client
from mcp.shared.exceptions import MCPError
from openai import APIStatusError

if __package__:
    from .agent import run_loop, server_parameters
    from .api_config import api_client, read_api_key
    from .mcp_server import create_server
    from .tracer import BASE_TASKS, run_checked
    from .policy import REFERENCE_WORKFLOWS, policy_for
else:
    from agent import run_loop, server_parameters
    from api_config import api_client, read_api_key
    from mcp_server import create_server
    from tracer import BASE_TASKS, run_checked
    from policy import REFERENCE_WORKFLOWS, policy_for



def tool_call(name, **arguments):
    return SimpleNamespace(
        status="completed", usage=None, output_text="",
        output=[SimpleNamespace(type="function_call", name=name,
                                arguments=json.dumps(arguments), call_id=f"call_{name}")],
    )


def final(text="The fixture result is 0.324."):
    return SimpleNamespace(status="completed", usage=None, output=[], output_text=text)


def fake_api(*responses):
    return SimpleNamespace(
        responses=SimpleNamespace(create=AsyncMock(side_effect=responses)),
    )


class AgentTests(unittest.IsolatedAsyncioTestCase):
    async def loop(self, api, *, policy="totals", **limits):
        server = create_server(BASE_TASKS["margin"], policy=policy)
        async with Client(server) as mcp:
            return await run_loop(api, mcp, model="offline-test", **limits)

    async def test_policy_rejection_then_math_repair_with_real_execution(self):
        api = fake_api(
            tool_call("run_workflow", code='query(SalesQuery(period=previous_period, aggregation="sum"))'),
            tool_call("run_workflow", code=REFERENCE_WORKFLOWS["totals"]["margin"] + '\n0'),
            tool_call("run_workflow", code=REFERENCE_WORKFLOWS["totals"]["margin"]),
            final(),
        )
        result = await self.loop(api)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["last_report"]["result"], 0.324)
        self.assertEqual(result["last_report"]["answer_check"], "passed")
        self.assertEqual(result["tool_calls"][1]["output"]["answer_check"], "failed")
        self.assertEqual(result["tool_calls"][0]["output"]["queries"], [])
        self.assertIn("literal_error", result["tool_calls"][0]["output"]["errors"][0])
        for call in api.responses.create.call_args_list:
            request = call.kwargs
            self.assertEqual(request["model"], "offline-test")
            self.assertFalse(request["store"])
            self.assertEqual({t["name"] for t in request["tools"]},
                             {"run_workflow"})
            self.assertTrue(all(t["type"] == "function" for t in request["tools"]))
            self.assertTrue(all(t["parameters"]["additionalProperties"] is False for t in request["tools"]))

    async def test_shell_extra_fields_and_old_review_tool_are_rejected(self):
        api = fake_api(
            tool_call("bash", command="echo should-never-run"),
            tool_call("run_workflow", code="0.324", expected=0.324),
            tool_call("evaluate_workflow", workflow="An unregistered workflow review request"),
            final("Everything passed"),
        )
        result = await self.loop(api)
        self.assertEqual(result["status"], "incomplete")
        self.assertIsNone(result["last_report"])
        self.assertTrue(all("error" in call["output"] for call in result["tool_calls"]))
        self.assertIn("not allowed", result["tool_calls"][0]["output"]["error"])
        self.assertIn("only", result["tool_calls"][1]["output"]["error"])
        self.assertIn("not allowed", result["tool_calls"][2]["output"]["error"])

    async def test_loop_limits_and_prose_never_create_a_passing_report(self):
        result = await self.loop(fake_api(final("Validated: 0.324")))
        self.assertEqual(result["status"], "incomplete")
        self.assertIsNone(result["last_report"])
        self.assertEqual(result["validation_status"], "not_run")
        self.assertIsNone(result["validated_answer"])
        for limit, kwargs in (("turn_limit", {"max_turns": 2}),
                              ("tool_limit", {"max_tool_calls": 2})):
            with self.subTest(limit=limit):
                api = fake_api(*(tool_call("unknown") for _ in range(3)))
                result = await self.loop(api, **kwargs)
                self.assertEqual(result["status"], limit)
                self.assertEqual(len(result["tool_calls"]), 2)

    async def test_wrong_prose_cannot_replace_checked_result(self):
        for prose in ("The validated margin is 99%.",
                      "Validated: the margin proves our new pricing caused higher profits."):
            with self.subTest(prose=prose):
                result = await self.loop(fake_api(
                    tool_call("run_workflow", code=REFERENCE_WORKFLOWS["totals"]["margin"]),
                    final(prose),
                ))
                self.assertEqual(result["validation_status"], "passed")
                self.assertEqual(result["validated_result"], result["last_report"]["result"])
                self.assertEqual(result["validated_answer"]["result"], 0.324)
                self.assertEqual(result["validated_answer"]["context"]["unit"], "ratio")
                self.assertEqual(result["validation_basis"], "fixture_answer")
                self.assertEqual(result["validated_answer"]["basis"], "fixture_answer")
                self.assertEqual(result["final_text"], prose)
                self.assertEqual(result["final_text_status"], "unverified")
                self.assertNotIn(prose, json.dumps(result["validated_answer"]))

    async def test_new_malformed_attempt_clears_prior_validation(self):
        for attempt, limits in (
            (tool_call("unknown", code="0.324"), {}),
            (tool_call("run_workflow", code="0.324", validated=True), {}),
            (tool_call("run_workflow", code="0.324"), {"max_tool_calls": 1}),
        ):
            with self.subTest(name=attempt.output[0].name, limits=limits):
                result = await self.loop(fake_api(
                    tool_call("run_workflow", code=REFERENCE_WORKFLOWS["totals"]["margin"]),
                    attempt, final("The previous validation proves this new answer is right."),
                ), **limits)
                self.assertEqual(result["tool_calls"][0]["output"]["answer_check"], "passed")
                self.assertEqual(result["validation_status"], "not_run")
                self.assertIsNone(result["last_report"])
                self.assertIsNone(result["validated_result"])
                self.assertIsNone(result["validated_answer"])

    async def test_last_failed_result_cannot_inherit_a_previous_pass(self):
        good = REFERENCE_WORKFLOWS["totals"]["margin"]
        for prior in ([], [tool_call("run_workflow", code=good)]):
            with self.subTest(previous_success=bool(prior)):
                result = await self.loop(fake_api(*prior,
                    tool_call("run_workflow", code=good + '\n0'), final("All checks passed.")))
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["validation_status"], "failed")
                self.assertEqual(result["last_report"]["result"], 0)
                self.assertIsNone(result["validated_result"])
                self.assertIsNone(result["validated_answer"])

    async def test_agent_repairs_workflow_for_changed_python_policy(self):
        api = fake_api(
            tool_call("run_workflow", code=REFERENCE_WORKFLOWS["totals"]["margin"]),
            tool_call("run_workflow", code=REFERENCE_WORKFLOWS["rows"]["margin"]), final(),
        )
        result = await self.loop(api, policy="rows")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["tool_calls"][0]["output"]["queries"], [])
        self.assertEqual(result["last_report"]["answer_check"], "passed")
        self.assertEqual(result["last_report"]["policy_calls"][0]["aggregation"], "rows")
        description = api.responses.create.call_args.kwargs["tools"][0]["description"]
        self.assertIn("Literal['rows']", description)

    async def test_api_errors_do_not_echo_credentials(self):
        error = APIStatusError(
            "Credential must-not-print was rejected",
            response=httpx2.Response(401, request=httpx2.Request("POST", "https://api.openai.com/v1/responses")),
            body=None,
        )
        api = fake_api()
        api.responses.create.side_effect = error
        result = await self.loop(api)
        self.assertEqual(result["status"], "api_error")
        self.assertNotIn("must-not-print", json.dumps(result))

    async def test_mcp_failure_retains_prior_evidence(self):
        api = fake_api(
            tool_call("run_workflow", code=REFERENCE_WORKFLOWS["totals"]["margin"]),
            tool_call("run_workflow", code="0"), final(),
        )
        server = create_server(BASE_TASKS["margin"])
        async with Client(server) as mcp:
            original = mcp.call_tool

            async def call(name, arguments):
                if arguments["code"] == "0":
                    raise MCPError(code=-32000, message="private upstream error")
                return await original(name, arguments)

            with patch.object(mcp, "call_tool", side_effect=call):
                result = await run_loop(api, mcp, model="offline-test")
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["tool_calls"][0]["output"]["answer_check"], "passed")
        self.assertIsNone(result["last_report"])
        self.assertEqual(result["validation_status"], "not_run")
        self.assertIsNone(result["validated_answer"])
        self.assertEqual(result["tool_calls"][1]["output"], {"error": "MCP request failed"})

    async def test_explicit_key_endpoint_and_private_server_environment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            keyfile = directory / "api.env"
            keyfile.write_text("OPENAI_API_KEY=test-only-key\n")
            with patch.dict(os.environ, {"OPENAI_API_KEY": "", "OPENAI_BASE_URL": "https://invalid.example",
                                         "OPENAI_ORG_ID": "account-org", "OPENAI_PROJECT_ID": "account-project"}):
                self.assertEqual(read_api_key(keyfile), "test-only-key")
                async with api_client(keyfile) as client:
                    self.assertEqual(str(client.base_url), "https://api.openai.com/v1/")
                    self.assertEqual(client.organization, "")
                    self.assertEqual(client.project, "")
            params = server_parameters(directory, "margin", "base", "totals")
            self.assertNotIn("OPENAI_API_KEY", params.env)
            self.assertEqual(params.env["HOME"], str(directory))
            self.assertEqual(params.env["CODEX_HOME"], str(directory))
            self.assertNotIn("test-only-key", json.dumps(params.model_dump(mode="json")))
            self.assertNotIn(str(keyfile), params.args)
            # The actual submitted program cannot open even a known credential path.
            report = run_checked(BASE_TASKS["margin"], f"open({str(keyfile)!r}).read()",
                                 policy=policy_for(BASE_TASKS["margin"]))
            self.assertEqual(report["execution"], "failed")
            self.assertIn("Permission", report["errors"][0])
            self.assertNotIn("test-only-key", json.dumps(report))


if __name__ == "__main__":
    unittest.main()

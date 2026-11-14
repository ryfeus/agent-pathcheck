"""Exercise MCP discovery and calls through a real stdio server subprocess."""

import json
from pathlib import Path
import sys
import unittest

from mcp import Client, StdioServerParameters

if __package__:
    from .tracer import BASE_TASKS
    from .policy import REFERENCE_WORKFLOWS
else:
    from tracer import BASE_TASKS
    from policy import REFERENCE_WORKFLOWS


SERVER = Path(__file__).with_name("mcp_server.py").resolve()
REFERENCE_PROGRAMS = REFERENCE_WORKFLOWS["totals"]


class MCPServerTests(unittest.IsolatedAsyncioTestCase):
    def client(self, *args):
        return Client(
            StdioServerParameters(command=sys.executable, args=[str(SERVER), *args]),
            read_timeout_seconds=10,
        )

    def report(self, result, execution, answer_check):
        self.assertFalse(result.is_error, result)
        report = result.structured_content
        self.assertIsInstance(report, dict)
        self.assertEqual(report["execution"], execution, report)
        self.assertEqual(report["answer_check"], answer_check, report)
        self.assertEqual(json.loads(result.content[0].text), report)
        json.dumps(report, allow_nan=False)
        return report

    async def test_discovery_supplies_context_and_only_code_argument(self):
        async with self.client() as client:
            listed = await client.list_tools()
            self.assertEqual([tool.name for tool in listed.tools], ["run_workflow"])
            tool = listed.tools[0]
            self.assertEqual(set(tool.input_schema["properties"]), {"code"})
            self.assertEqual(tool.input_schema["required"], ["code"])
            self.assertEqual(tool.input_schema["properties"]["code"]["type"], "string")
            self.assertIsNotNone(tool.output_schema)
            self.assertTrue(tool.annotations.read_only_hint)
            self.assertFalse(tool.annotations.open_world_hint)
            self.assertEqual(client.instructions, tool.description)
            self.assertIn(BASE_TASKS["margin"].question, tool.description)
            for context in ("question", "period", "previous_period", "class SalesQuery(PolicyModel)",
                            "Literal['2026-10']", "Literal['sum']", "query(request: SalesQuery)"):
                self.assertIn(context, tool.description)
            self.assertNotIn("0.324", tool.description)
            self.assertNotIn('"expected":', tool.description)

    async def test_reports_and_fresh_sessions_over_stdio(self):
        async with self.client() as client:
            # Guest prints must not corrupt the JSON-RPC stream on stdout.
            result = await client.call_tool("run_workflow", {
                "code": 'print("guest output")\nsaved = 123\n' + REFERENCE_PROGRAMS["margin"],
            })
            report = self.report(result, "succeeded", "passed")
            self.assertEqual(report["result"], 0.324)
            self.assertEqual(report["queries"][0]["params"], ["2026-10"])

            # Submitted tool arguments cannot override host-owned grading context.
            result = await client.call_tool("run_workflow", {
                "code": REFERENCE_PROGRAMS["margin"] + '\n0.25',
                "expected": 0.25, "fixture": "variant", "task": {"expected": 0.25},
            })
            self.report(result, "succeeded", "failed")

            result = await client.call_tool("run_workflow", {
                "code": REFERENCE_PROGRAMS["margin"] + '\n1 / 0',
            })
            failed = self.report(result, "failed", "not_run")
            self.assertEqual(failed["queries"][0]["rows"][0]["revenue_cents"], 10000000)

            result = await client.call_tool("run_workflow", {"code": "saved"})
            failed = self.report(result, "failed", "not_run")
            self.assertIn("NameError", failed["errors"][0])
            self.assertEqual(failed["queries"], [])

            result = await client.call_tool("run_workflow", {"code": REFERENCE_PROGRAMS["margin"]})
            self.report(result, "succeeded", "passed")

    async def test_host_selects_case_and_fixture_at_startup(self):
        for fixture, expected in (
            ("base", (100000.0, 0.324, -5.1)),
            ("variant", (120000.0, 0.25, -15.0)),
        ):
            for case, answer in zip(("revenue", "margin", "change"), expected):
                with self.subTest(fixture=fixture, case=case):
                    async with self.client("--case", case, "--fixture", fixture) as client:
                        self.assertIn(BASE_TASKS[case].question, client.instructions)
                        result = await client.call_tool("run_workflow", {"code": REFERENCE_PROGRAMS[case]})
                        report = self.report(result, "succeeded", "passed")
                        self.assertAlmostEqual(report["result"], answer, delta=1e-9)

    async def test_invalid_tool_calls_do_not_stop_server(self):
        async with self.client() as client:
            for arguments in ({}, {"code": 1}, {"code": None}):
                with self.subTest(arguments=arguments):
                    result = await client.call_tool("run_workflow", arguments)
                    self.assertTrue(result.is_error, result)
            unknown = await client.call_tool("unknown_tool", {})
            self.assertTrue(unknown.is_error)
            result = await client.call_tool("run_workflow", {"code": REFERENCE_PROGRAMS["margin"]})
            self.report(result, "succeeded", "passed")

    async def test_host_policy_changes_discovery_and_execution(self):
        async with self.client("--policy", "rows") as client:
            self.assertIn("Literal['rows']", client.instructions)
            self.assertIn("-> list[Sale]", client.instructions)
            result = await client.call_tool("run_workflow", {
                "code": REFERENCE_PROGRAMS["margin"], "policy": "totals",
            })
            rejected = self.report(result, "failed", "not_run")
            self.assertEqual(rejected["queries"], [])
            self.assertIn("literal_error", rejected["errors"][0])
            result = await client.call_tool("run_workflow", {
                "code": REFERENCE_WORKFLOWS["rows"]["margin"],
            })
            self.report(result, "succeeded", "passed")


if __name__ == "__main__":
    unittest.main()

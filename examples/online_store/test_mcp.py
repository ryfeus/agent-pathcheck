"""Real MCP/guest execution with mocked model responses; no network or API key."""

from contextlib import asynccontextmanager
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from mcp import Client, StdioServerParameters
from examples.agent_support import run_loop
from examples.online_store.mcp_server import SERVER, create_server, run_agent
from examples.online_store.planner import DEFAULT_PLAN
from examples.online_store.policy import build_policy
from examples.online_store.test_composition import api_for, call, final
from examples.online_store.workflows import request_code, workflow


class StoreMCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_lookup_and_frozen_host_policy(self):
        async with Client(create_server(case="revenue")) as client:
            tools = (await client.list_tools()).tools
            self.assertEqual([tool.name for tool in tools],["run_workflow","read_semantic"])
            self.assertEqual(set(tools[0].input_schema["properties"]),{"code"})
            self.assertIn("net_revenue",client.instructions)
            self.assertNotIn("36200000",client.instructions)
            page = await client.call_tool("read_semantic",{"name":"store.scope"})
            self.assertFalse(page.is_error)
            report = (await client.call_tool("run_workflow",{"code":"MetricResult(rows=[],total=0)"})).structured_content
            self.assertIsNone(report["validated_answer"])
            report = (await client.call_tool("run_workflow",{"code":workflow("revenue")})).structured_content
            self.assertEqual(report["validation_status"],"passed")
            self.assertEqual(report["result"]["total"],36200000)
            self.assertTrue((await client.call_tool("read_semantic",{"name":"../fixture.py"})).is_error)

    async def test_stdio_plan_file_compiles_without_model_access(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"plan.json"
            path.write_text(json.dumps(DEFAULT_PLAN))
            parameters = StdioServerParameters(command=sys.executable,args=[str(SERVER),"--case","composed","--plan-file",str(path)])
            async with Client(parameters,read_timeout_seconds=45) as client:
                self.assertIn("Gold customers",client.instructions)
                report = (await client.call_tool("run_workflow",{"code":workflow("composed",plan=DEFAULT_PLAN)})).structured_content
                self.assertEqual(report["validation_status"],"passed")
                self.assertEqual(report["result"]["total"],3)
                self.assertEqual(report["context"]["normalized_plan"]["group_by"],"supplier")

    async def test_repair_and_later_failure_control_publication(self):
        good = workflow("revenue")
        api = api_for(call("run_workflow",code="MetricResult(rows=[],total=0)"),call("run_workflow",code=good),
                      final("Revenue fell because of an unverified cause."))
        async with Client(create_server(case="revenue")) as client:
            result = await run_loop(api,client, model="offline-test")
        self.assertEqual(result["validation_status"],"passed")
        self.assertEqual(result["validated_result"]["total"],36200000)
        self.assertEqual(result["final_text_status"],"unverified")
        api = api_for(call("run_workflow",code=good),call("run_workflow",code="MetricResult(rows=[],total=0)"),final())
        async with Client(create_server(case="revenue")) as client:
            result = await run_loop(api,client, model="offline-test")
        self.assertNotEqual(result["validation_status"],"passed")
        self.assertIsNone(result["validated_answer"])

    async def test_natural_language_planning_then_existing_workflow_loop(self):
        api = api_for(call("submit_plan",plan_json=json.dumps(DEFAULT_PLAN)),
                      call("run_workflow",code=workflow("composed",plan=DEFAULT_PLAN)),final())
        @asynccontextmanager
        async def fake_client(_env):
            yield api
        with patch("examples.online_store.mcp_server.api_client",fake_client):
            result = await run_agent(model="offline-test", case="composed",question="Top suppliers by late deliveries to Gold customers last quarter")
        self.assertEqual(result["planning"]["status"],"planned")
        self.assertEqual(result["validation_status"],"passed")
        self.assertEqual(result["validated_result"]["total"],3)
        self.assertEqual(result["model"], "offline-test")
        self.assertGreaterEqual(len(api.responses.create.call_args_list), 3)
        for request in api.responses.create.call_args_list:
            self.assertEqual(request.kwargs["model"], "offline-test")

    async def test_planning_failure_never_launches_execution(self):
        api = api_for(final("Unsupported"))
        @asynccontextmanager
        async def fake_client(_env):
            yield api
        with patch("examples.online_store.mcp_server.api_client",fake_client), patch("examples.online_store.mcp_server.server_parameters") as start:
            result = await run_agent(model="offline-test", case="composed",question="An unsupported question")
        start.assert_not_called()
        self.assertEqual(result["status"],"planning_failed")
        self.assertIsNone(result["validated_answer"])

    async def test_agent_replay_flag_gates_publication(self):
        code = workflow("revenue").replace("total=sum(r.value for r in rows)","total=36200000")
        api = api_for(call("run_workflow",code=code),final())
        @asynccontextmanager
        async def fake_client(_env):
            yield api
        with patch("examples.online_store.mcp_server.api_client",fake_client):
            result = await run_agent(model="offline-test", case="revenue",replays=True)
        self.assertEqual(result["last_report"]["replay_check"],"failed")
        self.assertIsNone(result["validated_answer"])

    async def test_blocked_read_repair_requires_same_program_evidence_and_final_expression(self):
        good = workflow("product-margin")
        # Reproduce the real model's failures: an earlier blocked read, then
        # a refusal without fresh evidence, then an assigned but unreturned
        # decision. Only the complete workflow may publish an abstention.
        request = request_code(build_policy("product-margin"),0)
        no_read = 'Abstain(kind="data_block", rule_id="store.cost_missing", detail="Missing cost")'
        assigned = good.removesuffix("\nresult")
        api = api_for(call("run_workflow",code=f"query({request})"),
                      call("run_workflow",code=no_read),call("run_workflow",code=assigned),
                      call("run_workflow",code=good),final())
        async with Client(create_server(case="product-margin")) as client:
            result = await run_loop(api,client, model="offline-test")
        for invocation in result["tool_calls"][:3]:
            self.assertIsNone(invocation["output"]["validated_answer"])
        self.assertEqual(result["validation_status"],"passed")
        self.assertEqual(result["validated_result"]["rule_id"],"store.cost_missing")


if __name__ == "__main__":
    unittest.main()

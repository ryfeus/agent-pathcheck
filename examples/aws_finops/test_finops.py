"""Real SQLite/Monty/MCP tests; only model replies are mocked in the loop test."""

import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from mcp import Client, StdioServerParameters

from examples.aws_finops.mcp_server import create_server
from examples.aws_finops import tracer
from examples.aws_finops.queries import (
    candidates_statement, cost_statement, create_schema, evidence_statement, execute_statement,
)
from examples.aws_finops.tracer import COST_WORKFLOW, FIXTURE, rightsizing_workflow, run_checked
from examples.sql_math_tracer.agent import run_loop
from examples.sql_math_tracer.tracer import _make_query


EVIDENCE = '''\
query(CandidatesQuery(account=account))
query(EvidenceQuery(account=account, metric="cpu"))
query(EvidenceQuery(account=account, metric="memory"))
'''
SERVER = Path(__file__).with_name("mcp_server.py").resolve()


class CoreQueryTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.addCleanup(self.connection.close)
        self.connection.row_factory = sqlite3.Row
        create_schema(self.connection)
        self.connection.executescript(FIXTURE.read_text())
        self.trace = []
        self.sql_query = _make_query(self.connection, self.trace)

    def test_sqlite_enforces_the_declared_relationships(self):
        self.assertEqual(self.connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(self.connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        for sql in (
            "INSERT INTO recommendations VALUES ('rec-orphan', 'i-missing', 'm6i.large', 0.5, 100)",
            "INSERT INTO observations VALUES ('i-missing', 'cpu', 10.0, 14)",
            "DELETE FROM instances WHERE instance_id = 'i-web-a'",
            "UPDATE recommendations SET target_type='invented-type' WHERE recommendation_id='rec-web-a'",
            "DELETE FROM instance_types WHERE instance_type='m6i.xlarge'",
        ):
            with self.subTest(sql=sql), self.assertRaisesRegex(sqlite3.IntegrityError, "FOREIGN KEY"):
                self.connection.execute(sql)

    def test_core_joins_preserve_account_and_metric_scope(self):
        self.connection.executescript("""
            INSERT INTO instances VALUES ('i-dev', 'development', 'm6i.xlarge', 'x86_64');
            INSERT INTO recommendations VALUES ('rec-dev', 'i-dev', 'm6i.large', 0.5, 99999);
            INSERT INTO observations VALUES ('i-dev', 'cpu', 1.0, 14);
        """)
        for account, count in (("production", 10), ("development", 1)):
            with self.subTest(account=account):
                request = SimpleNamespace(account=account, metric="cpu")
                candidates = execute_statement(self.sql_query, candidates_statement(request))
                evidence = execute_statement(self.sql_query, evidence_statement(request))
                self.assertEqual(len(candidates), count)
                self.assertEqual(len(evidence), count)
                self.assertTrue(all(row["metric"] == "cpu" for row in evidence))
                self.assertEqual({row["instance_id"] for row in candidates},
                                 {row["instance_id"] for row in evidence})
                self.assertEqual("i-dev" in {row["instance_id"] for row in candidates},
                                 account == "development")
        memory = execute_statement(self.sql_query, evidence_statement(
            SimpleNamespace(account="production", metric="memory")))
        self.assertEqual(len(memory), 9)
        self.assertTrue(all(row["metric"] == "memory" for row in memory))
        self.assertNotIn("i-web-b", {row["instance_id"] for row in memory})

    def test_compiled_queries_keep_values_as_bound_parameters(self):
        # Exercise compilation independently of the stricter Pydantic request.
        account = "production' OR 1=1 --"
        for builder, metric in ((cost_statement, "NetAmortizedCost"),
                                (candidates_statement, "cpu"), (evidence_statement, "cpu")):
            with self.subTest(builder=builder.__name__):
                statement = builder(SimpleNamespace(account=account, month="2026-10", metric=metric))
                self.assertEqual(execute_statement(self.sql_query, statement), [])
                self.assertNotIn(account, self.trace[-1]["sql"])
                self.assertEqual(self.trace[-1]["params"][0], account)


class FinOpsTests(unittest.TestCase):
    def test_six_reference_cases_use_real_queries_and_independent_answers(self):
        for fixture, cost, cpu, memory in (("base", 590000, 50000, 43000),
                                           ("variant", 640000, 49500, 47500)):
            with self.subTest(fixture=fixture, case="costs"):
                report = run_checked(COST_WORKFLOW, case="costs", fixture=fixture)
                self.assertEqual(report["answer_check"], "passed", report)
                self.assertEqual(report["result"]["total_change_cents"], cost)
                self.assertEqual(len(report["queries"]), 2)
                self.assertEqual(report["queries"][0]["params"],
                                 ["production", "2026-09", "NetAmortizedCost"])
                changes = {r["service"]: r["change_cents"] for r in report["result"]["services"]}
                self.assertEqual(changes["CloudFront"], -30000)
                self.assertIn("ECS", changes)
            for policy, total in (("cpu-only", cpu), ("memory-required", memory)):
                with self.subTest(fixture=fixture, policy=policy):
                    report = run_checked(rightsizing_workflow(policy), fixture=fixture, policy=policy)
                    self.assertEqual(report["answer_check"], "passed", report)
                    self.assertEqual(report["result"]["monthly_savings_cents"], total)
                    self.assertEqual(len(report["queries"]), 2 if policy == "cpu-only" else 3)

    def test_cost_contract_rejects_wrong_scope_basis_and_period_before_sql(self):
        valid = 'CostQuery(account=account, month=period, metric="NetAmortizedCost", group_by="SERVICE")'
        for code in (valid.replace("account=account", 'account="development"'),
                     valid.replace("month=period", 'month="2026-11"'),
                     valid.replace("NetAmortizedCost", "UnblendedCost"),
                     valid.replace('"SERVICE"', '"LINKED_ACCOUNT"')):
            with self.subTest(code=code):
                report = run_checked(f"query({code})", case="costs")
                self.assertEqual(report["execution"], "failed")
                self.assertEqual(report["queries"], [])
                self.assertEqual(report["policy_events"][0]["status"], "rejected")
                self.assertIn("literal_error", report["errors"][0])

    def test_proposals_require_observed_evidence_in_this_workflow(self):
        proposal = 'RightsizeProposal(recommendation_id="rec-web-a")'
        for prefix, missing in (("", "Fetch candidates"),
                                ('query(CandidatesQuery(account=account))\n', "Fetch cpu"),
                                (EVIDENCE.split('query(EvidenceQuery(account=account, metric="memory"))')[0],
                                 "Fetch memory")):
            with self.subTest(missing=missing):
                report = run_checked(prefix + proposal)
                self.assertEqual(report["execution"], "failed")
                self.assertIn(missing, report["errors"][0])
        # A previous invocation's observations must not carry into the next one.
        self.assertEqual(run_checked(rightsizing_workflow())["answer_check"], "passed")
        self.assertEqual(run_checked(proposal)["queries"], [])
        self.assertIn("Fetch candidates", run_checked(proposal)["errors"][0])

    def test_evidence_and_recommendation_constraints_use_host_rows(self):
        for recommendation, error in (("rec-web-b", "Fetch memory"),
                                      ("rec-worker-b", "memory p95"),
                                      ("rec-batch-a", "cpu p95"),
                                      ("rec-search-a", "Performance risk"),
                                      ("rec-arm-a", "architecture")):
            with self.subTest(recommendation=recommendation):
                report = run_checked(EVIDENCE + f'RightsizeProposal(recommendation_id="{recommendation}")')
                self.assertEqual(report["execution"], "failed")
                self.assertEqual(len(report["queries"]), 3)
                self.assertIn(error, report["errors"][0])
        fabricated = EVIDENCE + 'RightsizeProposal(recommendation_id="rec-web-b", memory_p95=0.0)'
        self.assertIn("extra_forbidden", run_checked(fabricated)["errors"][0])
        # A dict nested in a plan still runs the proposal's evidence validator.
        nested = EVIDENCE + 'RightsizingPlan(proposals=[{"recommendation_id":"rec-web-b"}], monthly_savings_cents=11000)'
        self.assertIn("Fetch memory", run_checked(nested)["errors"][0])

    def test_policy_switch_requires_more_queries_and_changes_the_plan(self):
        old = rightsizing_workflow("cpu-only")
        self.assertEqual(run_checked(old, policy="cpu-only")["answer_check"], "passed")
        rejected = run_checked(old, policy="memory-required")
        self.assertEqual(rejected["execution"], "failed")
        self.assertEqual(len(rejected["queries"]), 2)
        failures = [e for e in rejected["policy_events"] if e["status"] == "rejected"]
        self.assertTrue(any("Fetch memory" in str(event) for event in failures))
        repaired = run_checked(rightsizing_workflow())
        self.assertEqual(repaired["answer_check"], "passed")
        self.assertEqual(len(repaired["queries"]), 3)
        self.assertEqual(repaired["result"]["monthly_savings_cents"], 43000)
        self.assertNotIn("rec-web-b", str(repaired["result"]))
        self.assertTrue(any(e["status"] == "rejected" for e in repaired["policy_events"]))
        self.assertEqual(repaired["errors"], [])  # Caught rejections remain in events.

    def test_workflow_can_read_the_required_metrics_from_the_policy(self):
        code = rightsizing_workflow().replace("for metric in ('cpu', 'memory'):",
                                             "for metric in required_metrics:")
        for policy, count in (("cpu-only", 2), ("memory-required", 3)):
            with self.subTest(policy=policy):
                report = run_checked(code, policy=policy)
                self.assertEqual(report["answer_check"], "passed", report)
                self.assertEqual(len(report["queries"]), count)

    def test_policy_and_answer_grading_are_separate(self):
        wrong = rightsizing_workflow().replace("monthly_savings_cents=total", "monthly_savings_cents=1")
        report = run_checked(wrong)
        self.assertEqual(report["execution"], "failed")
        self.assertEqual(report["contract_check"], "failed")
        self.assertEqual(report["answer_check"], "not_run")
        self.assertIsNone(report["validated_result"])
        duplicate = EVIDENCE + '''\
p = RightsizeProposal(recommendation_id="rec-web-a")
RightsizingPlan(proposals=[p, p], monthly_savings_cents=24000)
'''
        self.assertIn("at most one", run_checked(duplicate)["errors"][0])
        self.assertIn("validated RightsizingPlan", run_checked('{"validated": True}')["errors"][0])

    def test_no_raw_sql_and_failed_queries_leave_no_observed_evidence(self):
        for code in ('query("SELECT * FROM instances")',
                     'query({"account": "production"})', 'COST_EXPECTED'):
            with self.subTest(code=code):
                report = run_checked(code)
                self.assertEqual(report["execution"], "failed")
                self.assertEqual(report["queries"], [])
        report = run_checked(EVIDENCE + '1 / 0')
        self.assertEqual(report["answer_check"], "not_run")
        self.assertEqual(len(report["queries"]), 3)
        json.dumps(report, allow_nan=False)


class FinOpsMCPTests(unittest.IsolatedAsyncioTestCase):
    def client(self, *args):
        return Client(StdioServerParameters(command=sys.executable, args=[str(SERVER), *args]),
                      read_timeout_seconds=15)

    async def test_stdio_discovery_and_host_bound_policy(self):
        async with self.client() as client:
            tools = (await client.list_tools()).tools
            self.assertEqual([t.name for t in tools], ["run_workflow", "read_semantic"])
            self.assertEqual(set(tools[0].input_schema["properties"]), {"code"})
            self.assertIn('required_metrics: tuple[str, ...] = (\'cpu\', \'memory\')', client.instructions)
            self.assertIn("@model_validator", client.instructions)
            self.assertNotIn("43000", client.instructions)
            self.assertNotIn("rec-web-a", client.instructions)
            failed = await client.call_tool("run_workflow", {"code": rightsizing_workflow("cpu-only"),
                                                             "policy": "cpu-only"})
            self.assertFalse(failed.is_error)
            self.assertEqual(failed.structured_content["execution"], "failed")
            result = await client.call_tool("run_workflow", {"code": rightsizing_workflow()})
            self.assertEqual(result.structured_content["answer_check"], "passed")
            self.assertEqual(json.loads(result.content[0].text), result.structured_content)
            self.assertTrue((await client.call_tool("run_workflow", {"code": 1})).is_error)

    async def test_stdio_cost_case_and_variant(self):
        async with self.client("--case", "costs", "--fixture", "variant") as client:
            result = await client.call_tool("run_workflow", {"code": COST_WORKFLOW})
            self.assertEqual(result.structured_content["answer_check"], "passed")
            self.assertEqual(result.structured_content["result"]["total_change_cents"], 640000)

    async def test_shared_agent_loop_repairs_against_real_policy(self):
        def response(code, index):
            return SimpleNamespace(status="completed", usage=None, output_text="", output=[
                SimpleNamespace(type="function_call", name="run_workflow",
                                arguments=json.dumps({"code": code}), call_id=f"call_{index}")])
        api = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(side_effect=[
            response(rightsizing_workflow("cpu-only"), 1), response(rightsizing_workflow(), 2),
            SimpleNamespace(status="completed", usage=None, output=[], output_text="Verified savings: $9999 per month"),
        ])))
        async with Client(create_server()) as client:
            result = await run_loop(api, client, model="offline-test")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["tool_calls"][0]["output"]["execution"], "failed")
        self.assertEqual(result["last_report"]["answer_check"], "passed")
        self.assertEqual(result["validation_status"], "passed")
        self.assertEqual(result["validated_result"], result["last_report"]["result"])
        answer = result["validated_answer"]
        self.assertEqual(answer["result"]["monthly_savings_cents"], 43000)
        self.assertEqual([p["recommendation_id"] for p in answer["result"]["proposals"]],
                         ["rec-web-a", "rec-worker-a", "rec-cache-a", "rec-api-a", "rec-api-b"])
        self.assertEqual(answer["context"]["account"], "production")
        self.assertEqual(answer["context"]["unit"], "cents")
        self.assertEqual(result["final_text_status"], "unverified")
        self.assertEqual(result["final_text"], "Verified savings: $9999 per month")
        for request in api.responses.create.call_args_list:
            self.assertEqual([t["name"] for t in request.kwargs["tools"]], ["run_workflow", "read_semantic"])

    async def test_domain_valid_answer_survives_benchmark_failure_but_not_a_later_contract_failure(self):
        def response(code, index):
            return SimpleNamespace(status="completed", usage=None, output_text="", output=[
                SimpleNamespace(type="function_call", name="run_workflow",
                                arguments=json.dumps({"code": code}), call_id=f"call_{index}")])
        for invalid_last in (False, True):
            with self.subTest(invalid_last=invalid_last):
                replies = [response(COST_WORKFLOW, 1)]
                if invalid_last:
                    replies.append(response(COST_WORKFLOW.replace("sum(changes.values())", "0"), 2))
                replies.append(SimpleNamespace(status="completed", usage=None, output=[],
                                               output_text="Verified change: $999999"))
                api = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(side_effect=replies)))
                with patch.object(tracer, "COST_EXPECTED", {"base": (0, [])}):
                    async with Client(create_server(case="costs")) as client:
                        result = await run_loop(api, client, model="offline-test")
                self.assertEqual(result["tool_calls"][0]["output"]["answer_check"], "failed")
                self.assertEqual(result["validation_basis"], "domain_contract")
                self.assertEqual(result["final_text_status"], "unverified")
                if invalid_last:
                    self.assertEqual(result["validation_status"], "failed")
                    self.assertIsNone(result["validated_answer"])
                else:
                    self.assertEqual(result["validation_status"], "passed")
                    self.assertEqual(result["validated_answer"]["basis"], "domain_contract")
                    self.assertEqual(result["validated_result"]["total_change_cents"], 590000)


if __name__ == "__main__":
    unittest.main()

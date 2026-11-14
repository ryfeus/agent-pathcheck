"""Offline regression checks for dataset solvability and honest benchmark scoring."""

import unittest

from mcp import Client

from examples.aws_finops.benchmark import grade_trial, offline_benchmark, summarize
from examples.aws_finops.benchmark_cases import scenarios, witness
from examples.aws_finops.mcp_server import create_server


class BenchmarkTests(unittest.TestCase):
    def test_every_solvable_case_and_deliberate_failure_has_the_expected_verdict(self):
        result = offline_benchmark(scenarios())
        self.assertEqual(result["checks"], 53)
        self.assertEqual(result["matched"], 53,
                         [(r["name"], r["report"]["errors"]) for r in result["rows"] if not r["matched"]])

    def test_oracles_have_hand_checked_answers_and_deterministic_data(self):
        cases = scenarios()
        self.assertEqual(cases, scenarios())
        self.assertEqual(cases["cost_basic"].expected["total_change_cents"], 48000)
        self.assertEqual(cases["cost_churn_refunds"].expected["total_change_cents"], -45)
        self.assertEqual(cases["cost_duplicates_decoys"].expected["total_change_cents"], 1)
        self.assertEqual(cases["cost_large_cents"].expected["total_change_cents"], 9007199254740994)
        self.assertEqual(cases["plan_basic"].expected["monthly_savings_cents"], 49500)
        self.assertEqual(cases["plan_risk_boundary"].expected["monthly_savings_cents"], 39700)
        self.assertEqual(cases["plan_large_cents"].expected["monthly_savings_cents"], 45035996273704990)
        self.assertEqual(cases["plan_large_cents"].expected["proposals"],
                         [{"recommendation_id": f"rec-{i:03}"} for i in range(7, 2, -1)])
        self.assertEqual(cases["plan_missing_memory"].expected["outcome"], "no_eligible_recommendations")

    def test_grader_separates_agent_failure_repairs_and_wrong_acceptance(self):
        scenario = scenarios()["cost_basic"]
        good = {"validation_status": "passed", "policy_events": [{"status": "rejected"}]}
        bad = {"validation_status": "failed", "errors": ["bad output"]}
        result = dict(status="completed", validation_status="passed", validated_result=scenario.expected,
                      tool_calls=[{"output": bad}, {"output": good}])
        grade = grade_trial(scenario, result)
        self.assertEqual(grade["outcome"], "solved")
        self.assertTrue(grade["repaired"])
        self.assertEqual(grade["failed_submissions"], 1)  # Caught candidate rejection is not a failed submission.
        result.update(validation_status="failed", validated_result=None,
                      tool_calls=[{"output": good}, {"output": bad}], final_text="Everything passed")
        self.assertEqual(grade_trial(scenario, result)["outcome"], "agent_failed")
        result.update(validation_status="passed", validated_result={"total_change_cents": 0})
        self.assertEqual(grade_trial(scenario, result)["outcome"], "incorrect_acceptance")
        self.assertEqual(grade_trial(scenario, {"status": "api_error"})["outcome"], "infrastructure_error")

    def test_blocked_cases_are_not_mislabeled_as_reasoning_failures(self):
        scenario = scenarios()["cost_101_services"]
        result = dict(status="completed", tool_calls=[{"output": {"errors": ["SQL row limit exceeded"]}}])
        self.assertEqual(grade_trial(scenario, result)["outcome"], "expected_block")
        self.assertEqual(grade_trial(scenario, {"status": "completed"})["outcome"], "unverified_block")
        result["tool_calls"].insert(0, {"output": {"validation_status": "passed"}})
        self.assertEqual(grade_trial(scenario, result)["outcome"], "unexpected_acceptance")
        rows = [dict(blocked_by=scenario.blocked_by, grade=grade_trial(scenario, result), agent=result)]
        self.assertEqual(summarize(rows)["solvable_trials"], 0)


class BenchmarkMCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_dataset_and_oracle_stay_host_owned(self):
        scenario = scenarios()["cost_100_services"]
        async with Client(create_server(**scenario.host_options())) as client:
            discovered = (await client.list_tools()).tools
            self.assertEqual(len(discovered), 2)
            self.assertEqual(set(discovered[0].input_schema["properties"]), {"code"})
            self.assertNotIn("Service-000", discovered[0].description)
            self.assertNotIn("expected_result", discovered[0].description)
            report = (await client.call_tool("run_workflow", {"code": witness(scenario)})).structured_content
        self.assertEqual(report["answer_check"], "passed")
        self.assertEqual(report["validated_result"], scenario.expected)


if __name__ == "__main__":
    unittest.main()

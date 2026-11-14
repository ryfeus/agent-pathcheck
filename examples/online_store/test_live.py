"""Offline checks for the opt-in live evaluator; no model API calls."""

from contextlib import redirect_stdout
from copy import deepcopy
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from examples.online_store.live import evaluate, scenarios, sweep
from examples.online_store.planner import DEFAULT_PLAN, PlanSpec


class LiveEvaluationTests(unittest.IsolatedAsyncioTestCase):
    def test_correct_answer_does_not_override_wrong_interpretation(self):
        result = dict(status="completed",validation_status="passed",
                      validated_result={"total":3,"rows":[{"key":"S-1","value":2},{"key":"S-2","value":1}]},
                      planning=dict(status="planned",plan=PlanSpec(**DEFAULT_PLAN).model_dump(),attempts=[{"accepted":True}]))
        spec = scenarios()["planner-suppliers"]
        self.assertTrue(evaluate(spec,result)["passed"])
        result["planning"]["plan"]["customer_tier"] = "all"
        grade = evaluate(spec,result)
        self.assertTrue(grade["answer_matches"])
        self.assertFalse(grade["plan_matches"])
        self.assertFalse(grade["passed"])

    def test_answer_validation_completion_and_exact_abstention_are_required(self):
        spec = scenarios()["revenue"]
        result = dict(status="completed",validation_status="passed",validated_result={"total":36200000})
        self.assertTrue(evaluate(spec,result)["passed"])
        for changes in ({"validation_status":"failed"}, {"status":"budget_exceeded"},
                        {"validated_result":{"total":1}}):
            with self.subTest(changes=changes):
                self.assertFalse(evaluate(spec,{**result,**changes})["passed"])
        result["validated_result"] = dict(kind="abstained",rule_id="store.cost_missing")
        self.assertTrue(evaluate(scenarios()["product-margin"],result)["passed"])
        self.assertFalse(evaluate(scenarios()["returns-blocked"],result)["passed"])

    def test_failed_planning_usage_is_counted_once(self):
        failed = dict(status="planning_failed",failure="unsupported_or_incomplete",
                      attempts=[{"accepted":False}],model_usage=[{"input_tokens":100,"output_tokens":10}])
        grade = evaluate(scenarios()["planner-suppliers"],failed)
        self.assertFalse(grade["passed"])
        self.assertEqual(grade["planning_attempts"],1)
        self.assertEqual((grade["input_tokens"],grade["output_tokens"]),(100,10))
        self.assertEqual(grade["failure"],"unsupported_or_incomplete")
        nested = dict(status="completed",validation_status="passed",validated_result={"total":36200000},
                      planning=deepcopy(failed),model_usage=[{"input_tokens":200,"output_tokens":20}])
        grade = evaluate(scenarios()["revenue"],nested)
        self.assertEqual((grade["input_tokens"],grade["output_tokens"]),(300,30))

    async def test_sweep_retains_failed_trials_without_sending_expected_answers(self):
        success = dict(status="completed",validation_status="passed",validated_result={"total":36200000})
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with patch("examples.online_store.live.run_agent",side_effect=[success,RuntimeError("private request")]) as agent, redirect_stdout(StringIO()):
                summary = await sweep(["revenue","shipments"],output,model="offline-test", concurrency=1)
            self.assertFalse(summary["passed"])
            self.assertEqual(summary["model"], "offline-test")
            self.assertTrue(summary["scenarios"]["revenue"]["passed"])
            self.assertFalse(summary["scenarios"]["shipments"]["passed"])
            for invocation in agent.call_args_list:
                self.assertNotIn("expected",invocation.kwargs)
                self.assertNotIn("expected_plan",invocation.kwargs)
                self.assertTrue(invocation.kwargs["replays"])
                self.assertEqual(invocation.kwargs["model"], "offline-test")
            for name, trial in summary["scenarios"].items():
                raw = (output/f"{name}.json").read_bytes()
                self.assertEqual(trial["sha256"],sha256(raw).hexdigest())
                self.assertNotIn(b"private request",raw)
            self.assertEqual(json.loads((output/"shipments.json").read_text())["error"],"RuntimeError")
            self.assertEqual(json.loads((output/"summary.json").read_text()),summary)
            with patch("examples.online_store.live.run_agent") as agent, self.assertRaises(ValueError):
                await sweep(["revenue"],output, model="offline-test")
            agent.assert_not_called()


if __name__ == "__main__":
    unittest.main()

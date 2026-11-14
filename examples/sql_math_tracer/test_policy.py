"""Policy feasibility tests using real Pydantic, Monty and SQLite."""

from dataclasses import replace
import unittest

if __package__:
    from .policy import REFERENCE_WORKFLOWS, policy_for
    from .tracer import BASE_ROWS, BASE_TASKS, VARIANT_ROWS, VARIANT_TASKS, run_checked
else:
    from policy import REFERENCE_WORKFLOWS, policy_for
    from tracer import BASE_ROWS, BASE_TASKS, VARIANT_ROWS, VARIANT_TASKS, run_checked


class PolicyTests(unittest.TestCase):
    def run_workflow(self, code, *, mode="totals", task=None, rows=BASE_ROWS):
        task = task or BASE_TASKS["margin"]
        return run_checked(task, code, rows=rows, policy=policy_for(task, mode))

    def test_both_policies_all_cases_and_fixtures(self):
        for tasks, rows in ((BASE_TASKS, BASE_ROWS), (VARIANT_TASKS, VARIANT_ROWS)):
            for mode, programs in REFERENCE_WORKFLOWS.items():
                for case, code in programs.items():
                    with self.subTest(mode=mode, case=case, expected=tasks[case].expected):
                        report = self.run_workflow(code, mode=mode, task=tasks[case], rows=rows)
                        self.assertEqual(report["answer_check"], "passed", report)
                        self.assertEqual(len(report["policy_calls"]), 2 if case == "change" else 1)
                        self.assertEqual(len(report["queries"]), len(report["policy_calls"]))

    def test_invalid_constructions_and_untyped_calls_never_query_database(self):
        for code, error in (
            ('query(SalesQuery(period=previous_period, aggregation="sum"))', "literal_error"),
            ('query(SalesQuery(period=period, aggregation="avg"))', "literal_error"),
            ('query(SalesQuery(period=202610, aggregation="sum"))', "literal_error"),
            ('query(SalesQuery(period=period))', "missing"),
            ('query(SalesQuery(period=period, aggregation="sum", sql="SELECT 1"))', "extra_forbidden"),
            ('query({"period": period, "aggregation": "sum"})', "validated SalesQuery"),
            ('query("SELECT * FROM sales")', "validated SalesQuery"),
        ):
            with self.subTest(code=code):
                report = self.run_workflow(code)
                self.assertEqual(report["execution"], "failed", report)
                self.assertEqual(report["answer_check"], "not_run")
                self.assertEqual(report["queries"], [])
                self.assertEqual(report["policy_calls"], [])
                self.assertIn(error, report["errors"][0])

    def test_same_workflow_rejected_after_policy_change_then_repaired(self):
        original = REFERENCE_WORKFLOWS["totals"]["margin"]
        self.assertEqual(self.run_workflow(original)["answer_check"], "passed")
        rejected = self.run_workflow(original, mode="rows")
        self.assertEqual(rejected["queries"], [])
        self.assertIn("Input should be 'rows'", rejected["errors"][0])
        repaired = self.run_workflow(REFERENCE_WORKFLOWS["rows"]["margin"], mode="rows")
        self.assertEqual(repaired["answer_check"], "passed")
        self.assertNotIn("SUM(", repaired["queries"][0]["sql"])
        self.assertEqual(len(repaired["queries"][0]["rows"]), 2)

    def test_task_context_changes_the_actual_python_type(self):
        task = replace(BASE_TASKS["margin"], period="2027-01")
        rows = (("2027-01", 10_000_000, 6_760_000),)
        policy = policy_for(task)
        self.assertIn("Literal['2027-01']", policy.contract)
        self.assertNotIn("2026-10", policy.contract)
        code = REFERENCE_WORKFLOWS["totals"]["margin"]
        self.assertEqual(self.run_workflow(code, task=task, rows=rows)["answer_check"], "passed")
        old_month = code.replace("period=period", 'period="2026-10"')
        self.assertEqual(self.run_workflow(old_month, task=task, rows=rows)["queries"], [])
        change_policy = policy_for(BASE_TASKS["change"])
        self.assertIn("Literal['2026-10', '2026-09']", change_policy.contract)

    def test_later_policy_failure_preserves_prior_execution_evidence(self):
        code = REFERENCE_WORKFLOWS["totals"]["margin"] + '\nSalesQuery(period=previous_period, aggregation="sum")'
        report = self.run_workflow(code)
        self.assertEqual(report["execution"], "failed")
        self.assertEqual(len(report["queries"]), 1)
        self.assertEqual(len(report["policy_calls"]), 1)
        self.assertEqual(report["queries"][0]["rows"][0]["revenue_cents"], 10_000_000)

    def test_policy_acceptance_is_separate_from_numeric_correctness(self):
        code = REFERENCE_WORKFLOWS["totals"]["margin"] + '\n0'
        wrong = self.run_workflow(code)
        self.assertEqual(wrong["execution"], "succeeded")
        self.assertEqual(wrong["answer_check"], "failed")
        self.assertEqual(self.run_workflow('0.324')["answer_check"], "failed")
        # Typed access does not prove that the final value depends on the rows.
        hardcoded = REFERENCE_WORKFLOWS["totals"]["margin"] + '\n0.324'
        self.assertEqual(self.run_workflow(hardcoded)["answer_check"], "passed")
        self.assertEqual(self.run_workflow(hardcoded, task=VARIANT_TASKS["margin"],
                                           rows=VARIANT_ROWS)["answer_check"], "failed")

    def test_database_results_are_validated_as_pydantic_values(self):
        report = self.run_workflow(REFERENCE_WORKFLOWS["rows"]["margin"], mode="rows",
                                   rows=(("2026-10", "bad-value", 10),))
        self.assertEqual(report["execution"], "failed")
        self.assertIn("validation error for Sale", report["errors"][0])
        self.assertEqual(len(report["queries"]), 1)


if __name__ == "__main__":
    unittest.main()

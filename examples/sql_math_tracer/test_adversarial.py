"""Attack the typed boundary and distinguish answers from derivation claims."""

from dataclasses import replace
from fractions import Fraction
import unittest

from examples.sql_math_tracer.policy import REFERENCE_WORKFLOWS, policy_for
from examples.sql_math_tracer.tracer import BASE_TASKS, run_checked


class TypedBoundaryTests(unittest.TestCase):
    def test_caught_invalid_result_batch_cannot_count_as_a_successful_query(self):
        for mode, aggregation in (("rows", "rows"), ("totals", "sum")):
            with self.subTest(mode=mode):
                # One good row precedes the invalid row: no partial acceptance.
                rows = (("2026-10", 100, 50), ("2026-10", 1.25, 1))
                task = BASE_TASKS["margin"]
                code = (f'try:\n    query(SalesQuery(period=period, aggregation={aggregation!r}))\n'
                        'except ValueError:\n    pass\n0.324')
                report = run_checked(task, code, rows=rows, policy=policy_for(task, mode))
                self.assertEqual(report["execution"], "succeeded", report)
                self.assertEqual(report["answer_check"], "failed", report)
                self.assertEqual(report["validation_status"], "failed")
                self.assertIsNone(report["validated_answer"])
                self.assertIn("successful typed query", str(report["errors"]))
                self.assertIn("rows", report["queries"][0])  # Keep the diagnostic evidence.
                self.assertEqual(report["policy_calls"][0]["result_check"], "failed")

    def test_caught_result_failure_allows_a_later_valid_query(self):
        task = replace(BASE_TASKS["margin"], expected=0.5, include_previous_period=True)
        rows = ((task.previous_period, 1.25, 1), (task.period, 100, 50))
        code = ('try:\n    query(SalesQuery(period=previous_period, aggregation="rows"))\n'
                'except ValueError:\n    pass\n' + REFERENCE_WORKFLOWS["rows"]["margin"])
        report = run_checked(task, code, rows=rows, policy=policy_for(task, "rows"))
        self.assertEqual(report["validation_status"], "passed", report)
        self.assertEqual([c["result_check"] for c in report["policy_calls"]], ["failed", "passed"])

    def test_invented_types_and_unexposed_pydantic_methods_cannot_query(self):
        for expression in (
            'type("SalesQuery", (), {"period":period, "aggregation":"sum"})()',
            'SalesQuery.model_construct(period=previous_period, aggregation="sum")',
            'SalesQuery(period=period, aggregation="sum").model_copy(update={"period":previous_period})',
        ):
            with self.subTest(expression=expression):
                task = BASE_TASKS["margin"]
                report = run_checked(task, f"query({expression})", policy=policy_for(task))
                self.assertEqual(report["execution"], "failed", report)
                self.assertEqual(report["queries"], [])
                self.assertIsNone(report["validated_answer"])

    def test_guest_field_assignments_do_not_change_the_host_request(self):
        task = BASE_TASKS["margin"]
        code = '''\
request = SalesQuery(period=period, aggregation="sum")
request.period = previous_period
request.aggregation = "rows"
t = query(request)
(t.revenue_cents - t.cogs_cents) / t.revenue_cents
'''
        report = run_checked(task, code, policy=policy_for(task))
        self.assertEqual(report["validation_status"], "passed", report)
        self.assertEqual(report["queries"][0]["params"], [task.period])
        self.assertEqual(report["policy_calls"][0]["aggregation"], "sum")


class ArithmeticMetamorphicTests(unittest.TestCase):
    def test_independent_revenue_and_cogs_changes_update_all_calculations(self):
        # Fresh source records, independent of the repository's two stock fixtures.
        original = (("2026-09", 500, 200), ("2026-09", 300, 150),
                    ("2026-10", 600, 240), ("2026-10", 400, 160))
        variants = [original,
                    tuple((p, r + (137 if p == "2026-10" else 0), c) for p, r, c in original),
                    tuple((p, r, c + (53 if p == "2026-10" else 0)) for p, r, c in original)]
        for rows in variants:
            amounts = {p: (sum(r for m, r, _ in rows if m == p),
                           sum(c for m, _, c in rows if m == p)) for p in ("2026-09", "2026-10")}
            margins = {p: Fraction(r - c, r) for p, (r, c) in amounts.items()}
            expected = {"revenue": Fraction(amounts["2026-10"][0], 100),
                        "margin": margins["2026-10"],
                        "change": 100 * (margins["2026-10"] - margins["2026-09"])}
            for mode, programs in REFERENCE_WORKFLOWS.items():
                for case, code in programs.items():
                    with self.subTest(rows=rows, mode=mode, case=case):
                        task = replace(BASE_TASKS[case], expected=float(expected[case]))
                        report = run_checked(task, code, rows=rows, policy=policy_for(task, mode))
                        self.assertEqual(report["answer_check"], "passed", report)
                        self.assertAlmostEqual(report["result"], float(expected[case]), delta=1e-9)

    def test_arithmetic_shortcuts_pass_only_when_the_authoritative_answer_matches(self):
        prefix = 'rows = query(SalesQuery(period=period, aggregation="rows"))\n'
        for expression in (
            "0.6",  # Ignore all values after the required read.
            "answer = sum(r.revenue_cents for r in rows)\nanswer = 0.6\nanswer",
            "6 / 10",  # Arbitrary constants are not prohibited.
            "0.6 + 0 * sum(r.revenue_cents for r in rows)",
            "sum(r.cogs_cents for r in rows) / sum(r.revenue_cents for r in rows)",
        ):
            for cogs in (400, 700):
                with self.subTest(expression=expression, cogs=cogs):
                    task = replace(BASE_TASKS["margin"], expected=(1000-cogs)/1000)
                    report = run_checked(task, prefix + expression, rows=((task.period, 1000, cogs),),
                                         policy=policy_for(task, "rows"))
                    matches = cogs == 400 and not expression.startswith("sum(")
                    self.assertEqual(report["execution"], "succeeded", report)
                    self.assertEqual(report["answer_check"], "passed" if matches else "failed")
                    self.assertEqual(report["contract_check"], "not_applicable")
                    self.assertEqual(report["validated_answer"] is not None, matches)


if __name__ == "__main__":
    unittest.main()

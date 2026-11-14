"""Integration tests: real Monty workers and fresh in-memory SQLite fixtures."""

import json
import time
import unittest

if __package__:  # Supports module invocation and discovery from this directory.
    from .tracer import (
        BASE_ROWS, BASE_TASKS, REFERENCE_PROGRAMS, VARIANT_ROWS, VARIANT_TASKS,
        WRONG_MONTH_PROGRAM, Task, run_checked, run_raw_sql_baseline,
    )
else:
    from tracer import (
        BASE_ROWS, BASE_TASKS, REFERENCE_PROGRAMS, VARIANT_ROWS, VARIANT_TASKS,
        WRONG_MONTH_PROGRAM, Task, run_checked, run_raw_sql_baseline,
    )


class TracerTests(unittest.TestCase):
    def test_raw_sql_requires_the_explicit_baseline_entry_point(self):
        task, code = BASE_TASKS["margin"], REFERENCE_PROGRAMS["margin"]
        with self.assertRaises(TypeError):
            run_checked(task, code)
        with self.assertRaisesRegex(ValueError, "run_raw_sql_baseline"):
            run_checked(task, code, policy=None)
        report = run_raw_sql_baseline(task, code)
        self.assertEqual(report["validation_status"], "passed")
        self.assertEqual(report["validated_answer"]["context"]["experiment"], "raw_sql_baseline")

    def check_report(self, report, execution, answer_check):
        self.assertEqual(report["execution"], execution, report)
        self.assertEqual(report["answer_check"], answer_check, report)
        # No nonfinite floats or unserializable guest objects may leak out.
        json.dumps(report, allow_nan=False)
        if execution == "failed":
            self.assertIsNone(report["result"])
            self.assertTrue(report["errors"])

    def test_known_answers_on_both_fixtures(self):
        fixtures = (
            (BASE_ROWS, BASE_TASKS, (100000.0, 0.324, -5.1)),
            (VARIANT_ROWS, VARIANT_TASKS, (120000.0, 0.25, -15.0)),
        )
        for rows, tasks, expected in fixtures:
            for name, answer in zip(("revenue", "margin", "change"), expected):
                with self.subTest(rows=rows, case=name):
                    report = run_raw_sql_baseline(tasks[name], REFERENCE_PROGRAMS[name], rows=rows)
                    self.check_report(report, "succeeded", "passed")
                    self.assertAlmostEqual(report["result"], answer, delta=1e-9)
                    self.assertEqual(report["errors"], [])
                    self.assertEqual(len(report["queries"]), 1)
                    query = report["queries"][0]
                    self.assertIn("FROM sales", query["sql"])
                    self.assertEqual(query["params"], {
                        "revenue": ["2026-10"],
                        "margin": {"period": "2026-10"},
                        "change": ["2026-10", "2026-09"],
                    }[name])
                    self.assertTrue(query["rows"])
                    if name == "margin":
                        self.assertEqual(query["rows"], [{
                            "revenue": 10000000 if rows == BASE_ROWS else 12000000,
                            "cogs": 6760000 if rows == BASE_ROWS else 9000000,
                        }])

    def test_wrong_query_and_math(self):
        wrong_math = REFERENCE_PROGRAMS["margin"].replace(
            '(r["revenue"] - r["cogs"])', 'r["cogs"]'
        )
        for name, code, actual in (
            ("revenue", WRONG_MONTH_PROGRAM, 80000.0),
            ("margin", wrong_math, 0.676),
        ):
            with self.subTest(case=name):
                report = run_raw_sql_baseline(BASE_TASKS[name], code)
                self.check_report(report, "succeeded", "failed")
                self.assertEqual(report["result"], actual)
                self.assertTrue(report["errors"])
                self.assertIn("rows", report["queries"][0])

    def test_invalid_answers(self):
        for expression in (
            'float("nan")', 'float("inf")', '-float("inf")', "True", "False",
            '{"validated": True}', "None", '"0.324"', "[0.324]", "10 ** 400",
        ):
            with self.subTest(expression=expression):
                report = run_raw_sql_baseline(
                    BASE_TASKS["margin"], 'query("SELECT 1 AS ignored")\n' + expression
                )
                self.check_report(report, "succeeded", "failed")
                self.assertIsNone(report["result"])
                self.assertTrue(report["errors"])

    def test_successful_query_is_required(self):
        for code in (
            "0.324",
            'try:\n    query("SELECT missing FROM sales")\nexcept ValueError:\n    pass\n0.324',
            'try:\n    query("-- no statement")\nexcept ValueError:\n    pass\n0.324',
        ):
            with self.subTest(code=code):
                report = run_raw_sql_baseline(BASE_TASKS["margin"], code)
                self.check_report(report, "succeeded", "failed")
                self.assertEqual(report["result"], 0.324)
                self.assertIn("successful SQL query", report["errors"][0])
        # A real SELECT with no matching rows still counts as a successful query.
        empty_result = run_raw_sql_baseline(
            BASE_TASKS["margin"], 'query("SELECT * FROM sales WHERE 0"); 0.324'
        )
        self.check_report(empty_result, "succeeded", "passed")
        self.assertEqual(empty_result["queries"][0]["rows"], [])

    def test_execution_errors_preserve_completed_queries(self):
        for code, query_count, query_succeeded in (
            ("if :", 0, False),
            ('query("SELECT missing FROM sales")', 1, False),
            ('query("SELECT SUM(revenue_cents) AS revenue FROM sales")\n1 / 0', 1, True),
        ):
            with self.subTest(code=code):
                report = run_raw_sql_baseline(BASE_TASKS["margin"], code)
                self.check_report(report, "failed", "not_run")
                self.assertEqual(len(report["queries"]), query_count)
                if query_count:
                    self.assertIn("rows" if query_succeeded else "error", report["queries"][0])
                if query_succeeded:
                    self.assertEqual(report["queries"][0]["rows"], [{"revenue": 18000000}])

    def test_read_only_protection_and_recovery(self):
        attempts = (
            "UPDATE sales SET revenue_cents = 0",
            "INSERT INTO sales VALUES ('2026-10', 1, 0)",
            "DELETE FROM sales",
            "CREATE TABLE forged (expected REAL)",
            "DROP TABLE sales",
            "ALTER TABLE sales ADD COLUMN expected REAL",
            "ATTACH DATABASE ':memory:' AS other",
            "PRAGMA query_only = OFF",
            "PRAGMA writable_schema = ON",
            "PRAGMA user_version = 999",
            "CREATE TEMP TABLE forged (expected REAL)",
            "SELECT load_extension('not-an-extension')",
            "WITH fake AS (SELECT 1) UPDATE sales SET revenue_cents = 0",
        )
        expected_rows = [
            {"period": p, "revenue_cents": r, "cogs_cents": c}
            for p, r, c in sorted(BASE_ROWS)
        ]
        for sql in attempts:
            with self.subTest(sql=sql):
                report = run_raw_sql_baseline(BASE_TASKS["margin"], f"query({sql!r})")
                self.check_report(report, "failed", "not_run")
                self.assertIn("authoriz", report["queries"][0]["error"])
                self.assertEqual(report["queries"][0]["sql"], sql)
                # Inspect the SAME connection after the denied operation.
                code = (
                    f"try:\n    query({sql!r})\nexcept ValueError:\n    pass\n"
                    'query("SELECT * FROM sales ORDER BY period, revenue_cents")\n'
                    + REFERENCE_PROGRAMS["margin"]
                )
                recovered = run_raw_sql_baseline(BASE_TASKS["margin"], code)
                self.check_report(recovered, "succeeded", "passed")
                self.assertEqual(recovered["errors"], [])
                self.assertIn("error", recovered["queries"][0])
                self.assertEqual(recovered["queries"][1]["rows"], expected_rows)

    def test_bounded_execution(self):
        runaway_sql = (
            "WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n) "
            "SELECT SUM(x) FROM n"
        )
        too_many_rows = (
            "WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<101) "
            "SELECT x FROM n"
        )
        for code, error in (
            ("while True:\n    pass", "timeout"),
            (f"query({runaway_sql!r})", "instruction budget"),
            (f"query({too_many_rows!r})", "row limit"),
        ):
            with self.subTest(error=error):
                started = time.monotonic()
                report = run_raw_sql_baseline(BASE_TASKS["margin"], code)
                self.assertLess(time.monotonic() - started, 10)
                self.check_report(report, "failed", "not_run")
                self.assertIn(error, " ".join(report["errors"]).lower())
                if report["queries"]:
                    self.assertIn("error", report["queries"][0])
        # Recover after interruption: a new query gets its own instruction budget.
        recovered = run_raw_sql_baseline(
            BASE_TASKS["margin"],
            f"try:\n    query({runaway_sql!r})\nexcept ValueError:\n    pass\n"
            + REFERENCE_PROGRAMS["margin"],
        )
        self.check_report(recovered, "succeeded", "passed")
        self.assertIn("instruction budget", recovered["queries"][0]["error"])

    def test_remaining_execution_budgets(self):
        for code, error in (
            ("#" * 10001, "10000 characters"),
            ('for i in range(11):\n    query("SELECT 1")', "query limit"),
            ('query("SELECT 1 " + " " * 10000)', "too large"),
            ('query("SELECT zeroblob(1000001)")', "too big"),
            ('x = "x" * 20000000\n0', "memory"),
            ('import time\ntime.sleep(10)\n0', "sleep"),
            ('for i in range(40):\n    try:\n        query("SELECT 1")\n'
             '    except ValueError:\n        pass\n0', "suspension"),
        ):
            with self.subTest(error=error):
                report = run_raw_sql_baseline(BASE_TASKS["margin"], code)
                self.check_report(report, "failed", "not_run")
                self.assertIn(error, " ".join(report["errors"]).lower())
                if error == "query limit":
                    self.assertEqual(len(report["queries"]), 11)
                    self.assertTrue(all("rows" in q for q in report["queries"][:10]))
                    self.assertIn("error", report["queries"][-1])

    def test_host_owned_expectation_and_fresh_sessions(self):
        for name in ("expected", "task", "BASE_TASKS", "connection", "run_raw_sql_baseline"):
            with self.subTest(name=name):
                report = run_raw_sql_baseline(BASE_TASKS["margin"], name)
                self.check_report(report, "failed", "not_run")
                self.assertIn("NameError", report["errors"][0])
        inspection = '''\
assert question == "What was October 2026 gross margin, as a ratio?"
assert "CREATE TABLE sales" in schema
assert period == "2026-10"
assert previous_period == "2026-09"
query("SELECT name FROM sqlite_schema WHERE type = 'table'")
query("SELECT * FROM sales LIMIT 1")
0.324
'''
        report = run_raw_sql_baseline(BASE_TASKS["margin"], inspection)
        self.check_report(report, "succeeded", "passed")
        self.assertEqual(report["queries"][0]["rows"], [{"name": "sales"}])
        self.assertEqual(set(report["queries"][1]["rows"][0]),
                         {"period", "revenue_cents", "cogs_cents"})
        first = run_raw_sql_baseline(BASE_TASKS["margin"], "saved = 123\n" + REFERENCE_PROGRAMS["margin"])
        self.check_report(first, "succeeded", "passed")
        second = run_raw_sql_baseline(BASE_TASKS["margin"], "saved")
        self.check_report(second, "failed", "not_run")
        self.assertEqual(second["queries"], [])

    def test_hardcoding_limit_is_explicit(self):
        code = 'query("SELECT 1 AS ignored"); 0.324'
        base = run_raw_sql_baseline(BASE_TASKS["margin"], code)
        variant = run_raw_sql_baseline(VARIANT_TASKS["margin"], code, rows=VARIANT_ROWS)
        self.check_report(base, "succeeded", "passed")
        self.check_report(variant, "succeeded", "failed")
        self.assertEqual(base["result"], variant["result"])
        self.assertEqual(base["queries"], variant["queries"])

    def test_numeric_comparison_contract(self):
        for expression, verdict in (
            ("100000", "passed"),
            ("100000.0 + 5e-10", "passed"),
            ("100000.0 + 2e-9", "failed"),
            ("100000.000001", "failed"),
        ):
            with self.subTest(expression=expression):
                report = run_raw_sql_baseline(
                    BASE_TASKS["revenue"], 'query("SELECT 1")\n' + expression
                )
                self.check_report(report, "succeeded", verdict)
                self.assertIsNotNone(report["result"])

    def test_bound_parameters_and_single_statement(self):
        task = Task("Count rows for this literal period", 0, period="' OR 1=1 --")
        code = '''\
query("SELECT COUNT(*) AS n FROM sales WHERE period = :period", {"period": period})[0]["n"]
'''
        report = run_raw_sql_baseline(task, code)
        self.check_report(report, "succeeded", "passed")
        self.assertEqual(report["queries"][0]["params"], {"period": "' OR 1=1 --"})
        report = run_raw_sql_baseline(BASE_TASKS["margin"], 'query("SELECT 1; SELECT 2")')
        self.check_report(report, "failed", "not_run")
        self.assertIn("one statement", report["queries"][0]["error"])


if __name__ == "__main__":
    unittest.main()

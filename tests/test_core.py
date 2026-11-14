"""Exercise the reusable engine with a small domain that has no database or LLM."""

from contextlib import contextmanager
from typing import Literal
import unittest

from pydantic import model_validator

from agent_pathcheck import Domain, Invocation, Operation, PolicyModel, bind_operations, run, validate_model_output
from agent_pathcheck.presentation import validated_fields


class CoreTests(unittest.TestCase):
    def domain(self, *, close_error=False):
        lifecycle = []

        @contextmanager
        def open_invocation(report):
            lifecycle.append("opened")
            observed = {}

            class Read(PolicyModel):
                key: Literal["good", "bad", "empty", "later"]

            class Number(PolicyModel):
                value: int

            class Total(PolicyModel):
                value: int

                @model_validator(mode="after")
                def match_observed(self):
                    if "values" not in observed:
                        raise ValueError("Read evidence before returning a total")
                    if self.value != sum(row.value for row in observed["values"]):
                        raise ValueError("Total must match observed values")
                    return self

            rows = {"good": [{"value": 2}, {"value": 4}],
                    "bad": [{"value": 2}, {"value": "invalid"}],
                    "empty": [], "later": [{"value": 9}]}

            def observe(request, values):
                observed["values"] = values

            query = bind_operations([
                Operation(Read, Number, lambda request: rows[request.key], observe),
            ], report["policy_calls"], report["policy_events"])
            try:
                yield Invocation({}, {"query": query}, {"Read": Read, "Total": Total},
                                 lambda value: validate_model_output(value, Total, report["policy_events"]))
            finally:
                lifecycle.append("closed")
                if close_error:
                    raise RuntimeError("cleanup failed")

        return Domain({"unit": "items"}, open_invocation), lifecycle

    def test_non_sql_domain_checks_results_and_starts_fresh_each_time(self):
        domain, lifecycle = self.domain()
        outcome = run('rows = query(Read(key="good"))\nTotal(value=sum(r.value for r in rows))', domain=domain)
        self.assertEqual(outcome.report["contract_check"], "passed", outcome.report)
        self.assertEqual(outcome.report["result"], {"value": 6})
        self.assertEqual(validated_fields(outcome.report)["validation_basis"], "domain_contract")
        self.assertNotIn("answer_check", outcome.report)
        self.assertNotIn("queries", outcome.report)
        failed = run('Total(value=6)', domain=domain).report
        self.assertEqual(failed["contract_check"], "failed", failed)
        self.assertIsNone(validated_fields(failed)["validated_answer"])
        self.assertEqual(lifecycle, ["opened", "closed", "opened", "closed"])

    def test_caught_invalid_batch_never_commits_partial_evidence(self):
        domain, _ = self.domain()
        report = run('try:\n    query(Read(key="bad"))\nexcept ValueError:\n    pass\nTotal(value=2)',
                     domain=domain).report
        self.assertEqual(report["policy_calls"][0]["result_check"], "failed")
        self.assertIn("Read evidence", str(report["errors"]))
        self.assertIsNone(report["result"])
        repaired = run('try:\n    query(Read(key="bad"))\nexcept ValueError:\n    pass\n'
                       'query(Read(key="good"))\nTotal(value=6)', domain=domain).report
        self.assertEqual(repaired["contract_check"], "passed", repaired)
        self.assertEqual([call["result_check"] for call in repaired["policy_calls"]], ["failed", "passed"])

    def test_empty_batch_is_completed_evidence(self):
        domain, _ = self.domain()
        report = run('query(Read(key="empty"))\nTotal(value=0)', domain=domain).report
        self.assertEqual(report["contract_check"], "passed", report)
        self.assertEqual(report["policy_calls"][0]["result_check"], "passed")
        self.assertEqual(run('Total(value=0)', domain=domain).report["contract_check"], "failed")

    def test_final_value_is_revalidated_after_later_observations(self):
        domain, _ = self.domain()
        report = run('query(Read(key="good"))\ntotal = Total(value=6)\n'
                     'query(Read(key="later"))\ntotal', domain=domain).report
        self.assertEqual(report["execution"], "succeeded")
        self.assertEqual(report["contract_check"], "failed")
        self.assertEqual([event["status"] for event in report["policy_events"]
                          if event["type"] == "Total"], ["accepted", "rejected"])
        self.assertIsNone(validated_fields(report)["validated_result"])

    def test_execution_failure_and_cleanup_failure_cannot_publish_a_result(self):
        for code, close_error in (("1 / 0", False), ('query(Read(key="good"))\nTotal(value=6)', True)):
            with self.subTest(code=code):
                domain, lifecycle = self.domain(close_error=close_error)
                outcome = run(code, domain=domain)
                self.assertEqual(outcome.report["execution"], "failed")
                self.assertIsNone(outcome.report["result"])
                self.assertIsNone(outcome.value)
                self.assertIsNone(validated_fields(outcome.report)["validated_answer"])
                self.assertEqual(lifecycle, ["opened", "closed"])

    def test_source_limit_rejects_before_opening_domain_resources(self):
        domain, lifecycle = self.domain()
        for code in ("x" * 10001, None):
            report = run(code, domain=domain).report
            self.assertEqual(report["execution"], "failed")
            self.assertIn("Source must", report["errors"][0])
        self.assertEqual(lifecycle, [])

    def test_execution_without_a_contract_never_claims_domain_validation(self):
        @contextmanager
        def open_invocation(report):
            yield Invocation({}, {})

        domain = Domain({}, open_invocation, has_output_contract=False)
        outcome = run("6", domain=domain)
        self.assertEqual(outcome.value, 6)
        self.assertEqual(outcome.report["execution"], "succeeded")
        self.assertEqual(outcome.report["contract_check"], "not_applicable")
        self.assertIsNone(outcome.report["result"])
        self.assertEqual(validated_fields(outcome.report)["validation_status"], "not_run")
        misconfigured = run("6", domain=Domain({}, open_invocation)).report
        self.assertEqual(misconfigured["execution"], "failed")
        self.assertIsNone(validated_fields(misconfigured)["validated_answer"])

    def test_dispatch_requires_exact_registered_type(self):
        class Read(PolicyModel):
            key: str

        class Subclass(Read):
            pass

        class Number(PolicyModel):
            value: int

        calls, events, executed = [], [], []
        query = bind_operations([Operation(Read, Number, lambda request: executed.append(request))], calls, events)
        for request in ({"key": "good"}, "raw query", Subclass(key="good")):
            with self.subTest(request=request), self.assertRaises(TypeError):
                query(request)
        self.assertEqual(calls, [])
        self.assertEqual(executed, [])

    def test_single_result_cardinality_is_checked_before_observation(self):
        class Read(PolicyModel):
            pass

        class Number(PolicyModel):
            value: int

        for rows in ([], [{"value": 1}, {"value": 2}]):
            with self.subTest(rows=rows):
                calls, events, observed = [], [], []
                query = bind_operations([
                    Operation(Read, Number, lambda request: rows,
                              lambda request, values: observed.extend(values), many=False),
                ], calls, events)
                with self.assertRaisesRegex(ValueError, "exactly one result"):
                    query(Read())
                self.assertEqual(calls[0]["result_check"], "failed")
                self.assertEqual(observed, [])


if __name__ == "__main__":
    unittest.main()

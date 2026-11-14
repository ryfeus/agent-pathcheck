"""Domain invariants use observed rows, independently of benchmark answers."""

from contextlib import contextmanager, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from examples.aws_finops import tracer
from examples.aws_finops.mcp_server import main
from examples.aws_finops.policy import Candidate, Evidence, ServiceCost, build_policy


COST_QUERIES = tracer.COST_WORKFLOW.split("changes =")[0]
EVIDENCE_QUERIES = '''\
query(CandidatesQuery(account=account))
query(EvidenceQuery(account=account, metric="cpu"))
query(EvidenceQuery(account=account, metric="memory"))
'''
CHANGES = [("EC2", 450000), ("EC2-Other", 60000), ("RDS", 50000),
           ("ECS", 40000), ("S3", 30000), ("Lambda", -10000), ("CloudFront", -30000)]
IDS = ["rec-web-a", "rec-worker-a", "rec-cache-a", "rec-api-a", "rec-api-b"]


def cost_output(changes=CHANGES, total=590000):
    return (f"CostComparison(total_change_cents={total}, services=["
            f"ServiceChange(service=s, change_cents=c) for s, c in {changes!r}])")


def plan_output(ids=IDS, total=43000, outcome=None):
    outcome = outcome or ("recommendations" if ids else "no_eligible_recommendations")
    return (f"RightsizingPlan(monthly_savings_cents={total}, proposals=["
            f"RightsizeProposal(recommendation_id=r) for r in {ids!r}], outcome={outcome!r})")


class UnusedOracle:
    def __getitem__(self, key):
        raise AssertionError("Semantic rejection must happen before fixture grading")


@contextmanager
def changed_fixture(sql):
    """Vary only data; use the real schema, adapter, Monty, and validators."""
    original = tracer.FIXTURE.read_text()
    with tempfile.TemporaryDirectory() as directory:
        fixture = Path(directory) / "fixture.sql"
        fixture.write_text(original + "\n" + sql)
        with patch.object(tracer, "FIXTURE", fixture):
            yield


class SemanticTests(unittest.TestCase):
    def assert_rejected_before_grading(self, code, *, case="rightsizing", error=None):
        with patch.object(tracer, "COST_EXPECTED", UnusedOracle()), \
                patch.object(tracer, "PLAN_EXPECTED", UnusedOracle()):
            report = tracer.run_checked(code, case=case)
        self.assertEqual(report["contract_check"], "failed", report)
        self.assertEqual(report["answer_check"], "not_run", report)
        self.assertEqual(report["validation_status"], "failed")
        self.assertIsNone(report["validated_answer"])
        self.assertTrue(any(e["status"] == "rejected" for e in report["policy_events"]))
        if error:
            self.assertIn(error, str(report["errors"]))
        return report

    def test_cost_fabrications_fail_without_an_answer_oracle(self):
        wrong_breakdown = CHANGES.copy()
        wrong_breakdown[0] = ("EC2", 450001)
        wrong_breakdown[1] = ("EC2-Other", 59999)  # Still the fixture's correct total.
        for name, changes, total in (
            ("wrong breakdown", wrong_breakdown, 590000),
            ("wrong arithmetic", CHANGES, 999999),
            ("missing service", CHANGES[:-1], 620000),
            ("duplicate service", CHANGES + [CHANGES[-1]], 560000),
            ("wrong order", list(reversed(CHANGES)), 590000),
        ):
            with self.subTest(name=name):
                self.assert_rejected_before_grading(COST_QUERIES + cost_output(changes, total), case="costs")

    def test_plans_require_exact_top_selection_and_observed_total(self):
        for name, ids, total in (
            ("fabricated total", IDS, 999999),
            ("missing highest", IDS[1:], 31000),
            ("fewer than five", IDS[:-1], 37000),
            ("empty despite eligible candidates", [], 0),
            ("wrong order", list(reversed(IDS)), 43000),
            ("duplicate instance", [IDS[0], IDS[0]], 24000),
            ("unobserved candidate", ["rec-invented"], 1),
            ("missing observation", ["rec-web-b"], 11000),
        ):
            with self.subTest(name=name):
                self.assert_rejected_before_grading(EVIDENCE_QUERIES + plan_output(ids, total))

    def test_matching_fabricated_oracle_cannot_override_contract_failure(self):
        with patch.object(tracer, "PLAN_EXPECTED", {("base", "memory-required"): (999999, IDS)}):
            report = tracer.run_checked(EVIDENCE_QUERIES + plan_output(total=999999))
        self.assertEqual(report["contract_check"], "failed")
        self.assertEqual(report["answer_check"], "not_run")

    def test_caught_bad_output_stays_in_trace_after_a_valid_repair(self):
        code = (EVIDENCE_QUERIES + "try:\n    " + plan_output(total=999999)
                + "\nexcept ValueError:\n    pass\n" + plan_output())
        report = tracer.run_checked(code, max_claim_attempts=None)
        self.assertEqual(report["contract_check"], "passed", report)
        self.assertEqual(report["validation_status"], "passed")
        events = [e["status"] for e in report["policy_events"] if e["type"] == "RightsizingPlan"]
        self.assertEqual(events, ["rejected", "accepted"])
        self.assertEqual(report["errors"], [])

    def test_missing_queries_and_caught_wrong_scope_do_not_supply_evidence(self):
        one_month = COST_QUERIES.split("after =")[0]
        self.assert_rejected_before_grading(one_month + cost_output(), case="costs", error="2026-10")
        wrong_scope = '''\
try:
    query(CostQuery(account="development", month=period,
                    metric="NetAmortizedCost", group_by="SERVICE"))
except ValueError:
    pass
'''
        report = self.assert_rejected_before_grading(one_month + wrong_scope + cost_output(), case="costs")
        self.assertEqual(len(report["queries"]), 1)
        missing_memory = EVIDENCE_QUERIES.split('query(EvidenceQuery(account=account, metric="memory"))')[0]
        self.assert_rejected_before_grading(missing_memory + plan_output(), error="Fetch memory")
        self.assert_rejected_before_grading('RightsizingPlan(proposals=[], monthly_savings_cents=0)',
                                           error="candidate query")

    def test_observed_cost_keys_require_every_scope_dimension(self):
        # The ledger keys exact typed requests; every wrong scope is rejected
        # before it can create completed evidence.
        for field, value in (("account", "development"), ("month", "2026-11"),
                             ("metric", "UnblendedCost"), ("group_by", "LINKED_ACCOUNT")):
            with self.subTest(field=field):
                query = dict(account="production", month="2026-09", metric="NetAmortizedCost", group_by="SERVICE")
                query[field] = value
                code = f"try:\n    query(CostQuery(**{query!r}))\nexcept ValueError:\n    pass\n"
                self.assert_rejected_before_grading(code + 'CostComparison(total_change_cents=0, services=[])',
                                                   case="costs", error="Query 2026-09")

    def test_empty_results_are_completed_queries_but_not_eligible_evidence(self):
        with changed_fixture("DELETE FROM observations WHERE metric = 'memory';"):
            report = tracer.run_checked(EVIDENCE_QUERIES + plan_output([], 0))
            self.assertEqual(report["validation_status"], "passed", report)
            self.assertEqual(report["validated_result"], {
                "proposals": [], "monthly_savings_cents": 0, "outcome": "no_eligible_recommendations"})
            self.assertEqual(report["queries"][-1]["rows"], [])
            self.assert_rejected_before_grading(
                EVIDENCE_QUERIES + plan_output(), error="evidence is unavailable")
            missing = EVIDENCE_QUERIES.split('query(EvidenceQuery(account=account, metric="memory"))')[0]
            self.assert_rejected_before_grading(missing + plan_output([], 0), error="required metric queries")
        with changed_fixture("DELETE FROM recommendations;"):
            report = tracer.run_checked(tracer.rightsizing_workflow())
            self.assertEqual(report["validation_status"], "passed", report)
            self.assertEqual(report["validated_result"]["outcome"], "no_eligible_recommendations")
            self.assertEqual(report["queries"][0]["rows"], [])
        with changed_fixture("DELETE FROM costs WHERE month = '2026-09';"):
            report = tracer.run_checked(tracer.COST_WORKFLOW, case="costs")
            self.assertEqual(report["contract_check"], "passed", report)
            self.assertEqual(report["queries"][0]["rows"], [])
            self.assertEqual(report["result"]["total_change_cents"], 2520000)

    def test_changed_cost_data_checks_ties_absences_reappearance_and_scope(self):
        with changed_fixture("""
            UPDATE costs SET cost_cents = 460000 WHERE service = 'RDS' AND month = '2026-10';
            INSERT INTO costs VALUES ('production','2026-10','CloudFront','NetAmortizedCost',30000);
            DELETE FROM costs WHERE service = 'ECS';
            INSERT INTO costs VALUES ('production','2026-11','EC2','NetAmortizedCost',99999999);
            INSERT INTO costs VALUES ('development','2026-09','RDS','NetAmortizedCost',99999999);
        """):
            report = tracer.run_checked(tracer.COST_WORKFLOW, case="costs")
            expected = [("EC2", 450000), ("EC2-Other", 60000), ("RDS", 60000),
                        ("S3", 30000), ("CloudFront", 0), ("Lambda", -10000)]
            self.assertEqual(report["execution"], "succeeded")
            self.assertEqual(report["contract_check"], "passed", report)
            self.assertEqual(report["result"]["total_change_cents"], 590000)
            self.assertEqual([(r["service"], r["change_cents"]) for r in report["result"]["services"]], expected)
            # Same total as the base oracle, but a different valid breakdown.
            self.assertEqual(report["answer_check"], "failed")
            self.assertEqual(report["validation_status"], "passed")
            self.assertEqual(report["validation_basis"], "domain_contract")
            self.assertEqual(report["validated_answer"]["result"], report["result"])
            self.assertEqual(report["errors"], [])
            self.assertTrue(report["benchmark_errors"])
            wrong_tie = expected.copy()
            wrong_tie[1:3] = reversed(wrong_tie[1:3])
            self.assert_rejected_before_grading(COST_QUERIES + cost_output(wrong_tie), case="costs")

    def test_demo_cli_presents_changed_data_independently_of_benchmark(self):
        output = io.StringIO()
        with changed_fixture("UPDATE costs SET cost_cents=cost_cents+1 WHERE service='RDS' AND month='2026-10';"), \
                patch("sys.argv", ["finops", "--demo", "--case", "costs"]), redirect_stdout(output):
            main()  # A failed benchmark must not cause a nonzero CLI exit.
        report = json.loads(output.getvalue())
        self.assertEqual(report["answer_check"], "failed")
        self.assertEqual(report["contract_check"], "passed")
        self.assertEqual(report["validation_status"], "passed")
        self.assertEqual(report["validated_answer"]["result"]["total_change_cents"], 590001)

    def test_changed_candidates_check_top_cutoff_and_tie_order(self):
        with changed_fixture("""
            INSERT INTO observations VALUES ('i-web-b','memory',50.0,14);
            UPDATE recommendations SET monthly_savings_cents = 7000 WHERE recommendation_id = 'rec-web-b';
            INSERT INTO instances VALUES ('i-dev','development','m6i.xlarge','x86_64');
            INSERT INTO recommendations VALUES ('rec-dev','i-dev','m6i.large',0.5,999999);
            INSERT INTO observations VALUES ('i-dev','cpu',1.0,14), ('i-dev','memory',1.0,14);
        """):
            report = tracer.run_checked(tracer.rightsizing_workflow())
            expected = ["rec-web-a", "rec-worker-a", "rec-cache-a", "rec-api-a", "rec-web-b"]
            self.assertEqual(report["contract_check"], "passed", report)
            self.assertEqual([p["recommendation_id"] for p in report["result"]["proposals"]], expected)
            self.assertEqual(report["result"]["monthly_savings_cents"], 44000)
            self.assertEqual(report["answer_check"], "failed")
            self.assertEqual(report["validation_status"], "passed")
            wrong_tie = expected[:3] + list(reversed(expected[3:]))
            self.assert_rejected_before_grading(EVIDENCE_QUERIES + plan_output(wrong_tie, 44000))
            self.assert_rejected_before_grading(EVIDENCE_QUERIES + plan_output())

    def test_coincidentally_correct_hardcodes_are_not_a_dataflow_proof(self):
        for case, code in (("costs", COST_QUERIES + cost_output()),
                           ("rightsizing", EVIDENCE_QUERIES + plan_output())):
            with self.subTest(case=case):
                self.assertEqual(tracer.run_checked(code, case=case)["validation_status"], "passed")
                variant = tracer.run_checked(code, case=case, fixture="variant")
                self.assertEqual(variant["contract_check"], "failed")
                self.assertEqual(variant["answer_check"], "not_run")

    def test_reference_workflow_selects_one_eligible_recommendation_per_instance(self):
        for recommendation, savings, risk, selected in (
            ("rec-web-a-alt", 11999, 0.5, "rec-web-a"),
            ("rec-web-0", 12000, 0.5, "rec-web-0"),  # Equal savings: ID breaks the tie.
            ("rec-web-a-alt", 50000, 2.0, "rec-web-a"),  # Skip ineligible alternatives first.
        ):
            with self.subTest(recommendation=recommendation, risk=risk), changed_fixture(
                f"INSERT INTO recommendations VALUES ('{recommendation}','i-web-a','m6i.xlarge',{risk},{savings});"
            ):
                report = tracer.run_checked(tracer.rightsizing_workflow())
                self.assertEqual(report["validation_status"], "passed", report)
                self.assertEqual(report["result"]["proposals"],
                                 [{"recommendation_id": r} for r in [selected, *IDS[1:]]])
                self.assertEqual(report["result"]["monthly_savings_cents"], 43000)

    def test_no_eligible_outcome_requires_complete_queries_and_zero_savings(self):
        with changed_fixture("UPDATE recommendations SET monthly_savings_cents=0;"):
            report = tracer.run_checked(tracer.rightsizing_workflow())
            self.assertEqual(report["validation_status"], "passed", report)
            self.assertEqual(report["validated_result"], {
                "proposals": [], "monthly_savings_cents": 0, "outcome": "no_eligible_recommendations"})
            self.assertEqual(len(report["queries"]), 3)
            self.assert_rejected_before_grading(EVIDENCE_QUERIES + plan_output([], 1), error="sum")
            self.assert_rejected_before_grading(
                EVIDENCE_QUERIES + plan_output([], 0, "recommendations"), error="Outcome")
            self.assert_rejected_before_grading(plan_output([], 0), error="candidate query")
        self.assert_rejected_before_grading(
            EVIDENCE_QUERIES + plan_output(outcome="no_eligible_recommendations"), error="Outcome")

    def test_target_catalog_excludes_unknown_types_and_grounds_architecture(self):
        with changed_fixture("UPDATE recommendations SET target_type='invented-type' WHERE recommendation_id='rec-web-a';"):
            report = tracer.run_checked(tracer.rightsizing_workflow())
            self.assertEqual(report["execution"], "failed", report)
            self.assertIn("FOREIGN KEY", str(report["errors"]))
            self.assertIsNone(report["validated_answer"])
        # The existing arm target has good risk/utilization but incompatible
        # architecture. Changing the trusted catalog changes the joined evidence.
        with changed_fixture("UPDATE instance_types SET architecture='arm64' WHERE instance_type='m6i.xlarge';"):
            report = self.assert_rejected_before_grading(
                EVIDENCE_QUERIES + 'RightsizeProposal(recommendation_id="rec-web-a")', error="architecture")
            candidate = next(r for r in report["queries"][0]["rows"] if r["recommendation_id"] == "rec-web-a")
            self.assertEqual(candidate["target_architecture"], "arm64")


class DomainInputTests(unittest.TestCase):
    def test_risk_percent_and_coverage_boundaries(self):
        candidate = dict(recommendation_id="r", instance_id="i", instance_type="m6i.xlarge",
                         architecture="x86_64", target_type="m6i.large", target_architecture="x86_64",
                         performance_risk=0.0, monthly_savings_cents=100)
        evidence = dict(instance_id="i", metric="cpu", p95_percent=0.0, days=14)
        for model, data, field, valid, invalid in (
            (Candidate, candidate, "performance_risk", [0.0, 1.0, 4.0], [-0.1, 4.1, float("nan"), float("inf")]),
            (Evidence, evidence, "p95_percent", [0.0, 60.0, 80.0, 100.0], [-0.1, 100.1, float("nan"), float("inf")]),
            (Evidence, evidence, "days", [0, 13, 14], [-1, 14.5, True]),
        ):
            for value in valid:
                with self.subTest(field=field, valid=value):
                    model.model_validate({**data, field: value})
            for value in invalid:
                with self.subTest(field=field, invalid=value), self.assertRaises(ValidationError):
                    model.model_validate({**data, field: value})

    def test_policy_thresholds_use_coverage_not_recency(self):
        for metric, percent, days, eligible in (("cpu", 60.0, 14, True), ("cpu", 60.1, 14, False),
                                              ("memory", 80.0, 14, True), ("memory", 80.1, 14, False),
                                              ("cpu", 20.0, 13, False), ("memory", 20.0, 0, False)):
            with self.subTest(metric=metric, percent=percent, days=days), changed_fixture(
                f"UPDATE observations SET p95_percent={percent}, days={days} "
                f"WHERE instance_id='i-web-a' AND metric='{metric}';"
            ):
                report = tracer.run_checked(tracer.rightsizing_workflow())
                self.assertEqual(report["contract_check"], "passed", report)
                self.assertEqual("rec-web-a" in [p["recommendation_id"] for p in report["result"]["proposals"]], eligible)

    def test_invalid_database_rows_are_rejected_before_evidence_is_recorded(self):
        # Even if a response has some valid rows and the guest catches its error,
        # none of that failed batch may authorize a proposal.
        code = "\n".join(f"try:\n    {line}\nexcept ValueError:\n    pass"
                         for line in EVIDENCE_QUERIES.splitlines())
        code += '\nRightsizeProposal(recommendation_id="rec-web-a")'
        for mutation, model in (
            ("UPDATE recommendations SET performance_risk=4.1 WHERE recommendation_id='rec-api-b';", "Candidate"),
            ("UPDATE observations SET p95_percent=101 WHERE instance_id='i-api-b' AND metric='memory';", "Evidence"),
            ("UPDATE observations SET days=-1 WHERE instance_id='i-api-b' AND metric='cpu';", "Evidence"),
        ):
            with self.subTest(mutation=mutation), changed_fixture(mutation):
                report = tracer.run_checked(code)
                self.assertEqual(report["execution"], "failed")
                self.assertEqual(report["contract_check"], "failed")
                self.assertEqual(report["answer_check"], "not_run")
                self.assertTrue(any(e["type"] == model and e["status"] == "rejected"
                                    for e in report["policy_events"]))
                self.assertIn("Fetch", str(report["errors"]))
                self.assertIsNone(report["validated_answer"])


if __name__ == "__main__":
    unittest.main()

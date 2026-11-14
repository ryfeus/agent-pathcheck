"""Additional boundary attacks; existing semantic/benchmark matrices are reused."""

from copy import deepcopy
import unittest

from examples.aws_finops.benchmark_cases import fleet, plan_scenario, witness
from examples.aws_finops.test_semantics import COST_QUERIES, EVIDENCE_QUERIES, IDS, plan_output
from examples.aws_finops.tracer import COST_WORKFLOW, FIXTURE, rightsizing_workflow, run_checked


class BoundaryAttackTests(unittest.TestCase):
    def assert_unvalidated(self, report):
        self.assertNotEqual(report["validation_status"], "passed", report)
        self.assertEqual(report["answer_check"], "not_run")
        self.assertIsNone(report["validated_result"])
        self.assertIsNone(report["validated_answer"])

    def test_invented_query_types_and_constructor_shortcuts_never_reach_sql(self):
        for expression, policy in (
            ('type("CandidatesQuery", (), {"account":account})()', "memory-required"),
            ('CandidatesQuery.model_construct(account="development")', "memory-required"),
            ('CandidatesQuery(account=account).model_copy(update={"account":"development"})', "memory-required"),
            ('CandidatesQuery.__pydantic_validator__', "memory-required"),
            ('EvidenceQuery(account="development", metric="cpu")', "memory-required"),
            ('EvidenceQuery(account=account, metric="disk")', "memory-required"),
            ('EvidenceQuery(account=account, metric="memory")', "cpu-only"),
        ):
            with self.subTest(expression=expression):
                report = run_checked(f"query({expression})", policy=policy)
                self.assert_unvalidated(report)
                self.assertEqual(report["queries"], [])
                self.assertEqual(report["policy_calls"], [])

    def test_guest_request_fields_cannot_change_host_account_or_metric(self):
        code = '''\
q = CandidatesQuery(account=account)
q.account = "development"
query(q)
e = EvidenceQuery(account=account, metric="cpu")
e.account = "development"
e.metric = "memory"
query(e)
RightsizeProposal(recommendation_id="rec-web-a")
'''
        report = run_checked(code)
        self.assert_unvalidated(report)
        self.assertEqual([q["params"] for q in report["queries"]],
                         [["production"], ["production", "cpu"]])
        self.assertIn("Fetch memory", str(report["errors"]))

    def test_fabricated_flags_values_and_fake_outputs_cannot_authorize_proposals(self):
        for fields in ('approved=True', 'cpu_observed=True, memory_observed=True',
                       'memory_p95=0.0, days=14', 'monthly_savings_cents=999999'):
            with self.subTest(fields=fields):
                report = run_checked(EVIDENCE_QUERIES +
                    f'RightsizeProposal(recommendation_id="rec-web-a", {fields})')
                self.assert_unvalidated(report)
                self.assertIn("extra_forbidden", str(report["policy_events"]))
        for expression in (
            'type("RightsizingPlan", (), {"proposals":[], "monthly_savings_cents":0})()',
            'RightsizingPlan(proposals=[type("RightsizeProposal", (), '
            '{"recommendation_id":"rec-web-a"})()], monthly_savings_cents=12000)',
            'RightsizingPlan.model_construct(proposals=[], monthly_savings_cents=999999)',
            'RightsizingPlan(proposals=[{"recommendation_id":"rec-web-a", "approved":True}], '
            'monthly_savings_cents=12000)',
        ):
            with self.subTest(expression=expression):
                self.assert_unvalidated(run_checked(EVIDENCE_QUERIES + expression))
        # Nested dictionaries ARE valid input syntax, but run the full validators.
        # They are not trusted evidence or already-validated proposal instances.
        code = EVIDENCE_QUERIES + (f"RightsizingPlan(proposals={[{'recommendation_id': r} for r in IDS]!r}, "
                                  "monthly_savings_cents=43000)")
        self.assertEqual(run_checked(code)["validation_status"], "passed")
        self.assert_unvalidated(run_checked(code, fixture="variant"))

    def test_mutating_guest_evidence_does_not_change_host_observations(self):
        attacks = (
            ('''\
candidates = query(CandidatesQuery(account=account))
for c in candidates:
    c.target_architecture = c.architecture
    c.performance_risk = 0.0
query(EvidenceQuery(account=account, metric="cpu"))
query(EvidenceQuery(account=account, metric="memory"))
RightsizeProposal(recommendation_id="rec-arm-a")
''', "architecture"),
            ('''\
query(CandidatesQuery(account=account))
query(EvidenceQuery(account=account, metric="cpu"))
memory = query(EvidenceQuery(account=account, metric="memory"))
for e in memory:
    e.instance_id = "i-web-b"
    e.p95_percent = 0.0
    e.days = 100
RightsizeProposal(recommendation_id="rec-web-b")
''', "evidence is unavailable"),
            (rightsizing_workflow().replace('ordered = sorted(candidates,',
                'for c in candidates:\n    c.monthly_savings_cents += 100000\nordered = sorted(candidates,'),
             "sum of host-observed"),
        )
        for code, error in attacks:
            with self.subTest(error=error):
                report = run_checked(code)
                self.assert_unvalidated(report)
                self.assertIn(error, str(report["errors"]))

    def test_caught_failed_cost_batch_does_not_establish_monthly_prerequisites(self):
        fixture = FIXTURE.read_text() + "\nUPDATE costs SET cost_cents=0.5 WHERE service='S3' AND month='2026-10';"
        before = COST_QUERIES.split("after =")[0]
        code = before + '''\
try:
    query(CostQuery(account=account, month=period, metric="NetAmortizedCost", group_by="SERVICE"))
except ValueError:
    pass
CostComparison(total_change_cents=0, services=[])
'''
        report = run_checked(code, case="costs", fixture_sql=fixture)
        self.assert_unvalidated(report)
        self.assertEqual(len(report["queries"]), 2)
        self.assertTrue(any(e["type"] == "ServiceCost" and e["status"] == "rejected"
                            for e in report["policy_events"]))
        self.assertIn("Query 2026-10 costs", str(report["errors"]))

    def test_successful_intermediate_model_cannot_authorize_later_fabrication(self):
        for case, good, final in (
            ("rightsizing", rightsizing_workflow(),
             'RightsizingPlan(proposals=[], monthly_savings_cents=0, outcome="no_eligible_recommendations")'),
            ("costs", COST_WORKFLOW, 'CostComparison(total_change_cents=0, services=[])'),
        ):
            with self.subTest(case=case):
                # Keep a successful constructor event, catch the next rejection,
                # then return a claim. Only the final expression may be certified.
                code = good + '\ntry:\n    ' + final + '\nexcept ValueError:\n    pass\n{"validated": True}'
                report = run_checked(code, case=case, max_claim_attempts=None)
                self.assert_unvalidated(report)
                self.assertEqual(report["execution"], "succeeded")
                output_name = "RightsizingPlan" if case == "rightsizing" else "CostComparison"
                self.assertEqual([e["status"] for e in report["policy_events"] if e["type"] == output_name],
                                 ["accepted", "rejected"])


class SelectionMetamorphicTests(unittest.TestCase):
    def test_better_candidate_and_savings_increase_displace_the_previous_cutoff(self):
        # Existing tests cover missing evidence, thresholds, architecture, ties,
        # empty sets and service churn. Add paired interventions on ranking.
        for count in (6, 8):
            original = fleet(count)
            base = plan_scenario("before", original, "ranking control")
            code = witness(base)
            base_report = run_checked(code, **base.host_options())
            self.assertEqual(base_report["validated_result"], base.expected)
            for mutation in ("increase", "add"):
                with self.subTest(count=count, mutation=mutation):
                    data = deepcopy(original)
                    if mutation == "increase":
                        rec, instance, target, risk, _ = data["recommendations"][-1]
                        data["recommendations"][-1] = (rec, instance, target, risk, 20000)
                    else:
                        rec = "rec-new"
                        data["instances"].append(("i-new", "production", "demo.xlarge", "x86_64"))
                        data["recommendations"].append((rec, "i-new", "demo.large", 0.5, 20000))
                        data["observations"] += [("i-new", m, 20.0, 14) for m in ("cpu", "memory")]
                    changed = plan_scenario("after", data, mutation)
                    report = run_checked(code, **changed.host_options())
                    self.assertEqual(report["answer_check"], "passed", report)
                    self.assertEqual(report["validated_result"], changed.expected)
                    self.assertEqual(report["result"]["proposals"],
                                     [{"recommendation_id": r} for r in [rec, "rec-000", "rec-001", "rec-002", "rec-003"]])
                    self.assertEqual(report["result"]["monthly_savings_cents"] -
                                     base_report["result"]["monthly_savings_cents"], 20000 - 9800)
                    stale = EVIDENCE_QUERIES + plan_output(
                        [p["recommendation_id"] for p in base.expected["proposals"]],
                        base.expected["monthly_savings_cents"])
                    stale_report = run_checked(stale, **changed.host_options())
                    self.assertEqual(stale_report["contract_check"], "failed")
                    self.assertEqual(stale_report["answer_check"], "not_run")


if __name__ == "__main__":
    unittest.main()

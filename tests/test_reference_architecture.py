"""Executable guarantees added by the reference architecture review."""

from contextlib import contextmanager
from dataclasses import replace
import unittest

from pydantic import model_validator

from agent_pathcheck import (Domain, Invocation, Operation, Policy, PolicyModel, PolicyViolation,
                             ResourceLimitExceeded, bind_operations, read_report, run, validate_model_output)
from agent_pathcheck.extensions.replay import Replay
from agent_pathcheck.presentation import validated_fields
from examples.aws_finops.benchmark_cases import scenarios, witness
from examples.aws_finops.policy import build_policy
from examples.aws_finops.test_semantics import IDS
from examples.aws_finops.tracer import COUNTERFACTUAL, replays_for, rightsizing_workflow, run_checked


from agent_pathcheck.safety import BANNED_CONSTRUCTS, unsupported_constructs

class ContractTests(unittest.TestCase):
    def test_rendered_contract_avoids_unsupported_constructs(self):
        old = "candidate = next((c for c in candidates if c.id == wanted), None)"
        self.assertEqual(unsupported_constructs(old), ["next_generator"])
        for case in ("costs", "rightsizing"):
            for mode in ("cpu-only", "memory-required"):
                self.assertEqual(unsupported_constructs(build_policy(case, mode).contract), [], BANNED_CONSTRUCTS)

    def test_question_preserves_selection_intent_in_checked_answer(self):
        report = run_checked(rightsizing_workflow())
        question = report["validated_answer"]["context"]["question"]
        for intent in ("five", "descending", "ID ascending", "one recommendation per instance", "no_eligible_recommendations"):
            self.assertIn(intent, question)

    def test_generated_constraints_remain_visible_in_contract(self):
        source = build_policy("rightsizing", "memory-required").contract
        for field in ("performance_risk: float = Field(..., ge=0, le=4", "p95_percent: float = Field(..., ge=0, le=100",
                      "days: int = Field(..., ge=0)", "metric: Literal['cpu', 'memory']"):
            self.assertIn(field, source)


class FailureTests(unittest.TestCase):
    def test_memory_word_in_policy_error_does_not_make_it_a_resource_limit(self):
        class Answer(PolicyModel):
            value: int

            @model_validator(mode="after")
            def check(self):
                raise PolicyViolation("decision_rule", "memory.utilization", "Memory utilization exceeds the 80% limit")

        @contextmanager
        def factory(report):
            yield Invocation({}, {}, {"Answer": Answer},
                             lambda value: validate_model_output(value, Answer, report["policy_events"]))
        report = run("Answer(value=1)", domain=Domain({}, factory)).report
        self.assertEqual(report["failure"]["class"], "policy")
        self.assertEqual(report["failure"]["rule_id"], "memory.utilization")

    def test_exact_monty_limits_and_unrelated_runtime_messages(self):
        @contextmanager
        def factory(report):
            yield Invocation({}, {})
        domain = Domain({}, factory, has_output_contract=False)
        for code, limit in [('x = "x" * 20000000', "max_memory"),
                            ('while True:\n    pass', "max_feed_duration_secs"),
                            ('import time\ntime.sleep(1)', "max_total_sleep_secs")]:
            with self.subTest(limit=limit):
                report = run(code, domain=domain).report
                self.assertEqual(report["failure"]["class"], "resource_limit", report["errors"])
                self.assertEqual(report["failure"]["limit"], limit)
        report = run('raise ValueError("memory exceeds an unrelated limit")', domain=domain).report
        self.assertEqual(report["failure"]["class"], "runtime")


class AbstentionTests(unittest.TestCase):
    def test_unneeded_request_block_cannot_authorize_abstention(self):
        class Read(PolicyModel):
            scope: str

        class Row(PolicyModel):
            value: int

        @contextmanager
        def factory(report):
            def execute(request):
                if request.scope == "unneeded":
                    raise ResourceLimitExceeded("max_rows", 100, "SQL row limit exceeded")
                return [{"value": 1}]
            query = bind_operations([Operation(Read, Row, execute)], report["policy_calls"], report["policy_events"])
            yield Invocation({}, {"query": query}, {"Read": Read}, lambda value: {"value": 1})

        code = ('try:\n    query(Read(scope="unneeded"))\nexcept ValueError:\n    pass\n'
                'Abstain(kind="resource_limit", rule_id="limit.max_rows", detail="blocked")')
        for required, accepted in ((None, False), (lambda _: [Read(scope="needed")], False),
                                   (lambda _: [Read(scope="unneeded")], True)):
            policy = Policy("probe", "1", factory, None, required_requests=required)
            report = run(code, domain=Domain({}, factory, policy=policy, allow_abstain=True)).report
            self.assertEqual(report["contract_check"] == "passed", accepted)
            if not accepted:
                self.assertEqual(report["failure"]["rule_id"], "abstain.unjustified")
                self.assertIsNone(validated_fields(report)["validated_answer"])


class ReplayTests(unittest.TestCase):
    def test_hardcoded_selection_fails_required_ranking_replay(self):
        code = ('candidates = query(CandidatesQuery(account=account))\n'
                'query(EvidenceQuery(account=account, metric="cpu"))\n'
                'query(EvidenceQuery(account=account, metric="memory"))\n'
                f'ids = {IDS!r}\n'
                'RightsizingPlan(proposals=[RightsizeProposal(recommendation_id=r) for r in ids], '
                'monthly_savings_cents=sum(c.monthly_savings_cents for c in candidates if c.recommendation_id in ids))')
        report = run_checked(code, replays=replays_for("rightsizing"), require_replays=True)
        self.assertEqual(report["contract_check"], "passed")
        self.assertEqual([trial["verdict"] for trial in report["replays"]], ["passed", "failed"])
        self.assertIsNone(report["validated_answer"])

    def test_unchanged_requires_explicit_host_exemption(self):
        case = scenarios()["cost_basic"]
        identity = Replay("identity", lambda initialize: initialize)
        for advisory in (False, True):
            report = run_checked(witness(case), **case.host_options(), replays=[identity], require_replays=not advisory)
            self.assertEqual(report["replay_check"], "inconclusive")
            self.assertEqual(report["validation_status"] == "passed", advisory)
        exempt = replace(identity, exemption=lambda domain, base, alternate: "Host explicitly permits this identity trial.")
        report = run_checked(witness(case), **case.host_options(), replays=[exempt], require_replays=True)
        self.assertEqual(report["replays"][0]["verdict"], "inconclusive")
        self.assertEqual(report["validation_status"], "passed")

    def test_failed_replays_cannot_be_exempted(self):
        replay = replace(COUNTERFACTUAL, exemption=lambda *args: self.fail("Do not exempt a failure"))
        from examples.aws_finops.test_semantics import COST_QUERIES, cost_output
        report = run_checked(COST_QUERIES + cost_output(), case="costs", replays=[replay], require_replays=True)
        self.assertEqual(report["replays"][0]["verdict"], "failed")
        self.assertIsNone(report["replays"][0]["exemption_reason"])
        self.assertIsNone(report["validated_answer"])

    def test_empty_answers_are_inconclusive_and_before_only_costs_are_exercised(self):
        for name in ("cost_empty_both", "plan_empty", "plan_missing_memory", "cost_empty_after"):
            case = scenarios()[name]
            report = run_checked(witness(case), **case.host_options(), replays=replays_for(case.case), require_replays=True)
            self.assertEqual(report["validation_status"], "passed", report["errors"])
            if name == "cost_empty_after":
                self.assertEqual(report["replay_check"], "passed")
            else:
                self.assertEqual(report["replay_check"], "inconclusive")
                self.assertTrue(all(trial["exemption_reason"] for trial in report["replays"]))

    def test_old_reports_keep_their_recorded_verdict(self):
        for data in ({}, {"schema_version": 1}):
            parsed = read_report({**data, "replay_check": "passed"})
            self.assertEqual(parsed.schema_version, 1)
            self.assertEqual(parsed.replay_check, "passed")
            self.assertEqual(parsed.timeline, [])

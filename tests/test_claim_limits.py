import unittest
from contextlib import contextmanager

from agent_pathcheck import Domain, Invocation, PolicyModel, run, validate_model_output
from examples.aws_finops.tracer import run_checked, rightsizing_workflow
from examples.aws_finops.test_semantics import EVIDENCE_QUERIES, plan_output


class ClaimLimitTests(unittest.TestCase):
    def test_caught_repair_and_legacy(self):
        code = EVIDENCE_QUERIES + 'try:\n    ' + plan_output(total=123) + '\nexcept ValueError:\n    pass\n' + plan_output()
        report = run_checked(code)
        self.assertEqual(report['failure']['rule_id'], 'claim.attempt_limit')
        self.assertEqual(report['contract_check'], 'failed')
        self.assertIsNone(report['validated_answer'])
        self.assertEqual(run_checked(code, max_claim_attempts=None)['contract_check'], 'passed')

    def test_filter_rejections_are_not_claims(self):
        for _ in range(2):
            report = run_checked(rightsizing_workflow())
            self.assertEqual(report['contract_check'], 'passed')
            self.assertEqual(report['claim_attempts'], {'RightsizingPlan': 1})
            self.assertTrue(any(e['status'] == 'rejected' for e in report['policy_events']))

    def test_aliases_and_caught_caps_cannot_publish(self):
        class Answer(PolicyModel):
            value: int

        @contextmanager
        def factory(report):
            yield Invocation({}, {}, {'Answer': Answer, 'Alias': Answer},
                             lambda v: validate_model_output(v, Answer, report['policy_events']),
                             frozenset({'Answer'}))

        code = 'a = Answer(value=1)\ntry:\n    Alias(value=2)\nexcept ValueError:\n    pass\na'
        for options, rule in [({'max_claim_attempts': 1}, 'claim.attempt_limit'),
                              ({'max_constructions': {'Answer': 1}}, 'limit.max_constructions')]:
            r = run(code, domain=Domain({}, factory, **options)).report
            self.assertEqual(r['failure']['rule_id'], rule)
            self.assertEqual(r['contract_check'], 'failed')
            self.assertEqual(r['claim_attempts'], {'Answer': 2})

    def test_cap_does_not_justify_abstention(self):
        code = 'try:\n    CandidatesQuery(account=account)\nexcept ValueError:\n    pass\nAbstain(kind="resource_limit", rule_id="limit.max_constructions", detail="blocked")'
        r = run_checked(code, max_constructions={'CandidatesQuery': 0})
        self.assertEqual(r['failure']['class'], 'resource_limit')
        self.assertIsNone(r['validated_answer'])

    def test_original_guessing_attack(self):
        prefix = rightsizing_workflow().split('RightsizingPlan(')[0]
        prefix = prefix.replace('total = 0\n', '').replace('    total += candidate.monthly_savings_cents\n', '')
        code = prefix + """plan = None
for guess in [10000, 20000, 30000, 40000, 43000, 50000]:
    try:
        plan = RightsizingPlan(proposals=proposals, monthly_savings_cents=guess)
        break
    except ValueError:
        pass
plan
"""
        legacy = run_checked(code, max_claim_attempts=None)
        self.assertEqual(legacy['answer_check'], 'passed')
        self.assertEqual(legacy['claim_attempts_rejected'], {'RightsizingPlan': 4})
        current = run_checked(code)
        self.assertEqual(current['failure']['rule_id'], 'claim.attempt_limit')
        self.assertIsNone(current['validated_answer'])

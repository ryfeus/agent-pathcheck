import unittest
from examples.aws_finops.semantic_cases import caveat_scenarios, cost_witness
from examples.aws_finops.tracer import run_checked, replays_for


class CaveatTests(unittest.TestCase):
    def test_churn_and_stable_witnesses(self):
        for scenario in caveat_scenarios().values():
            report=run_checked(cost_witness(),**scenario.host_options(),
                               replays=replays_for('costs'),require_replays=True)
            self.assertEqual(report['validation_status'],'passed',report['errors'])
            self.assertEqual(report['answer_check'],'passed')
            self.assertEqual([c['rule_id'] for c in report['validated_answer']['context']['caveats']],
                             scenario.expected['caveats'])

    def test_missing_extra_and_duplicate_caveats(self):
        for name, replacement in [('cost_caveats_churn','[]'),('cost_caveats_stable','["costs.service_churn"]'),
                                  ('cost_caveats_churn','["costs.service_churn", "costs.service_churn"]')]:
            code=cost_witness().replace('["costs.service_churn"] if churn else []',replacement)
            r=run_checked(code,**caveat_scenarios()[name].host_options())
            self.assertEqual(r['failure']['rule_id'],'costs.caveats_required')
            self.assertIsNone(r['validated_answer'])

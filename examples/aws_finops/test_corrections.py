from pathlib import Path
import unittest
from unittest.mock import patch

from agent_pathcheck.acceptance import policy_acceptance
from examples.aws_finops.acceptance import acceptance, factory, semantic_check
from examples.aws_finops.benchmark_cases import scenarios, witness
from examples.aws_finops.correction_impact import correction_impact
from examples.aws_finops.semantic_cases import minimum_scenarios, semantic_witness
from examples.aws_finops.tracer import run_checked, LIMITS
from examples.aws_finops import policy as policy_module


class CorrectionTests(unittest.TestCase):
    def test_minimum_boundaries_and_unchanged_control(self):
        for s in minimum_scenarios().values():
            report=run_checked(semantic_witness(s),**s.host_options())
            self.assertEqual(report['answer_check'],'passed',report['errors'])
        boundary=minimum_scenarios()['min_savings_boundary']
        self.assertEqual(boundary.expected['proposals'],[{'recommendation_id':'rec-002'},{'recommendation_id':'rec-001'}])
        self.assertEqual(minimum_scenarios()['min_savings_basic'].expected,scenarios()['plan_basic'].expected)

    def test_acceptance_passes_existing_and_corrected_modes(self):
        for corrected in (False,True):
            result=acceptance(corrected)
            self.assertTrue(result['passed'],[c for c in result['checks'] if not c['passed']])

    def test_weakened_utilization_is_rejected_by_negative_probe(self):
        scenario=scenarios()['plan_utilization_boundary']
        bad=witness(scenario).replace(' or e[0] > (60.0 if metric == "cpu" else 80.0)','')
        probe=dict(name='utilization_bypass',scenario=scenario.name,code=bad,rule_id='rightsizing.utilization')
        args=(factory,{scenario.name:scenario},{scenario.name:witness(scenario)},[probe],{})
        self.assertTrue(policy_acceptance(*args,semantic_check=semantic_check,limits=LIMITS)['passed'])
        def weakened_limit(metric):
            return 100.0
        with patch.object(policy_module,'utilization_limit',weakened_limit):
            result=policy_acceptance(*args,semantic_check=semantic_check,limits=LIMITS)
        self.assertFalse(result['passed'])
        negative=[c for c in result['checks'] if c['kind']=='negative_probe'][0]
        self.assertFalse(negative['passed'])
        self.assertEqual(negative['report']['contract_check'],'passed')

    def test_verified_sample_impact(self):
        root=Path(__file__).resolve().parents[2]/'tests'/'fixtures'
        result=correction_impact([root/'semantic_correction_sample.json'],
                                  manifest_path=root/'semantic_correction_manifest.json')
        self.assertEqual(result['summary'],{'unchanged':1,'invalidated':1,'outside_correction':1})
        self.assertEqual(result['settings'],{'max_claim_attempts':None})

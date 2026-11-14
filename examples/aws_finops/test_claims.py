"""Composable claims preserve the information in every historical cost scenario."""

import unittest

from agent_pathcheck.evidence import Evidence, evidence_context
from agent_pathcheck.extensions.engine import Engine
from agent_pathcheck.presentation import validated_fields
from agent_pathcheck.safety import unsupported_constructs
from examples.aws_finops.benchmark_cases import scenarios
from examples.aws_finops.claims import money, projection
from examples.aws_finops.claims_cases import CLAIMS_READS, CLAIMS_WITNESS, claims_scenarios, negative_probes
from examples.aws_finops.policy import build_policy
from examples.aws_finops.tracer import run_checked


def options(scenario):
    return {**scenario.host_options(), 'case': 'cost-claims', 'semantic_hash': 'claims-test'}


class FinOpsClaimsTests(unittest.TestCase):
    def test_required_claims_match_source_and_qualifiers(self):
        suffixes = [
            'Answer(claims={"only":CostTotal(source=after)},text="Checked.")',
            'Answer(claims={"delta":CostDelta(before=CostTotal(source=after),after=CostTotal(source=before)), '
            '"services":ServiceChanges(before=before,after=after)},text="Checked.")',
            'Answer(claims={"delta":CostDelta(before=CostTotal(source=before),after=CostTotal(source=after)), '
            '"services":ServiceChanges(before=before,after=after,top=2)},text="Checked.")',
        ]
        for code in suffixes:
            report = run_checked(CLAIMS_READS + code, **options(scenarios()['cost_basic']))
            self.assertIn('claims.required_missing', [error['rule_id'] for event in report['policy_events']
                                                     for error in event.get('errors', [])])
            self.assertIsNone(report['validated_answer'])
        extra = CLAIMS_WITNESS.replace('"services": services', '"services": services, "total":CostTotal(source=after)')
        self.assertEqual(run_checked(extra, **options(scenarios()['cost_basic']))['answer_check'], 'passed')

    def test_required_contract_line_and_question_selection(self):
        policy = build_policy('cost-claims', 'memory-required', question='comparison')
        expected = '# This question requires: ' + '; '.join(
            spec.description() for spec in policy.parameters['required_claims'])
        self.assertEqual(policy.contract.splitlines()[-1], expected)
        for case, question in [('cost-claims', 'unknown'), ('costs', 'comparison')]:
            with self.assertRaises(ValueError):
                build_policy(case, 'memory-required', question=question)

    def test_equivalence_all_cost_scenarios(self):
        tested = blocked = 0
        with Engine() as engine:
            for scenario in scenarios().values():
                if scenario.case != 'costs':
                    continue
                with self.subTest(scenario=scenario.name):
                    report = run_checked(CLAIMS_WITNESS, **options(scenario), engine=engine)
                    if scenario.blocked_by:
                        blocked += 1
                        self.assertIsNone(report['validated_answer'])
                        self.assertIn(scenario.blocked_by, str(report['errors']))
                    else:
                        tested += 1
                        self.assertEqual(report['answer_check'], 'passed', report['errors'])
                        self.assertEqual(projection(report['result']), scenario.expected)
        self.assertEqual((tested, blocked), (12, 1))

    def test_negative_probes_keep_original_rules_and_latch(self):
        with Engine() as engine:
            for name, scenario, code, rule in negative_probes():
                with self.subTest(probe=name):
                    report = run_checked(code, **claims_scenarios()['claims_' + scenario].host_options(), engine=engine)
                    self.assertIsNone(report['validated_answer'])
                    if name == 'claims_raw_dict':
                        self.assertEqual(report['failure']['class'], 'contract')
                        self.assertIn(rule, str(report['errors']))
                    else:
                        self.assertIn(rule, [error['rule_id'] for event in report['policy_events']
                                            for error in event.get('errors', [])])
                        self.assertEqual(report['failure']['rule_id'], 'claims.rejection_limit')

    def test_oversized_top_and_invalid_sizes(self):
        extra = 'extra = ServiceChanges(before=before, after=after, top=999)\n'
        code = CLAIMS_READS + extra + CLAIMS_WITNESS[len(CLAIMS_READS):].replace(
            '"services": services', '"services": services, "extra": extra')
        report = run_checked(code, **options(scenarios()['cost_basic']))
        self.assertEqual(report['contract_check'], 'passed', report['errors'])
        claims = report['result']['claims']
        self.assertEqual(claims['extra']['rows'], claims['services']['rows'])
        for top in ('0', '-1', 'True', '1.0', '"one"'):
            report = run_checked(CLAIMS_WITNESS.replace('top=None', f'top={top}'),
                                 **options(scenarios()['cost_basic']))
            self.assertIsNone(report['validated_answer'])

    def test_rendered_answer_preserves_scope_and_exact_money(self):
        report = run_checked(CLAIMS_WITNESS, **options(scenarios()['cost_large_cents']))
        answer = report['validated_answer']
        self.assertEqual(answer, validated_fields(report)['validated_answer'])
        self.assertEqual(answer['claims'][0]['type'], 'CostDelta')
        self.assertEqual(answer['claims'][0]['scope']['before']['source']['month'], '2026-09')
        self.assertIn('policy_hash', answer['context'])
        self.assertIn('$90,071,992,547,409.94', answer['text'])
        self.assertEqual(money(-1), '-$0.01')
        self.assertEqual(money(0), '$0.00')

    def test_contract_safety_and_nested_forgery(self):
        policy = build_policy('cost-claims', 'memory-required')
        self.assertEqual(unsupported_constructs(policy.contract), [])
        for name in ('CostTotal', 'CostDelta', 'ServiceChanges', 'Answer'):
            self.assertIn('class ' + name + '(', policy.contract)
        code = CLAIMS_WITNESS.replace('CostTotal(source=before)', '{"source":before,"value":1}')
        report = run_checked(code, **options(scenarios()['cost_basic']))
        self.assertIsNone(report['validated_answer'])

import ast
from dataclasses import replace
import inspect
import unittest

from agent_pathcheck.rules import Level
from examples.aws_finops import policy as module
from examples.aws_finops.rules import DESCRIPTIONS, TESTED
from examples.aws_finops.benchmark import offline_benchmark
from examples.aws_finops.benchmark_cases import scenarios
from examples.aws_finops.hygiene import leak_problems


class RuleTests(unittest.TestCase):
    def test_hash_separates_semantics_from_prose(self):
        p = module.build_policy('costs', 'memory-required')
        key = 'costs.total'
        updated = {**p.rules, key: replace(p.rules[key], why='A revised explanation.')}
        self.assertEqual(p.policy_hash, replace(p, rules=updated).policy_hash)
        updated[key] = replace(updated[key], level=Level.CHECKED)
        self.assertNotEqual(p.policy_hash, replace(p, rules=updated).policy_hash)
        with self.assertRaises(TypeError):
            p.rules[key] = updated[key]

    def test_domain_rules_have_sites_and_safe_prose(self):
        literals = {n.value for n in ast.walk(ast.parse(inspect.getsource(module)))
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        self.assertTrue(set(DESCRIPTIONS) <= literals)
        for case in ('costs', 'rightsizing'):
            for rule in module.build_policy(case, 'memory-required').rules.values():
                self.assertEqual(leak_problems(rule.summary + '\n' + rule.why), [])

    def test_observed_rules_are_registered_and_tested_rules_are_triggered(self):
        triggered = set()
        for row in offline_benchmark(scenarios())['rows']:
            report = row['report']
            case = scenarios()[row.get('scenario', row['name'])].case
            rules = module.build_policy(case, 'memory-required').rules
            ids = {e['rule_id'] for event in report['policy_events'] for e in event.get('errors', [])}
            if report.get('failure', {}).get('rule_id') if report.get('failure') else False:
                ids.add(report['failure']['rule_id'])
            self.assertTrue(ids <= rules.keys(), ids - rules.keys())
            if row['kind'] == 'deliberate_bad_program':
                triggered.update(ids)
        self.assertTrue(TESTED <= triggered, TESTED - triggered)

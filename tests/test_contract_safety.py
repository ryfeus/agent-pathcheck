"""Every published contract must be safe to imitate in Monty."""

import unittest

from agent_pathcheck.safety import unsupported_constructs, BANNED_CONSTRUCTS
from examples.aws_finops.policy import build_policy, modes_for
from examples.sql_math_tracer.policy import policy_for
from examples.sql_math_tracer.tracer import BASE_TASKS


class SafetyTests(unittest.TestCase):
    def test_old_construct_is_rejected(self):
        self.assertEqual(unsupported_constructs('next((x for x in xs), None)'), ['next_generator'])

    def test_all_contracts(self):
        contracts = [policy_for(task, mode).contract for task in BASE_TASKS.values()
                     for mode in ('totals', 'rows')]
        contracts += [build_policy(case, mode).contract for case in ('costs', 'rightsizing', 'cost-claims')
                      for mode in modes_for(case)]
        for source in contracts:
            self.assertEqual(unsupported_constructs(source), [], BANNED_CONSTRUCTS)

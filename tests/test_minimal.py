"""The Level 0 example works without database or model integrations."""

import unittest
from agent_pathcheck import run
from agent_pathcheck.presentation import validated_fields
from examples.minimal.workflow import Read, WORKFLOW, make_domain


class MinimalTests(unittest.TestCase):
    def test_public_exports_are_available_from_this_checkout(self):
        from pathlib import Path
        import agent_pathcheck
        self.assertEqual(Path(agent_pathcheck.__file__).resolve().parents[1],
                         Path(__file__).resolve().parents[1])
        namespace = {}
        exec("from agent_pathcheck import *", namespace)
        for name in agent_pathcheck.__all__:
            self.assertIs(namespace[name], getattr(agent_pathcheck, name))

    def test_observed_sum_and_empty_batch(self):
        for values, total in (((2, 4), 6), ((), 0), ((-2, 9), 7)):
            report = run(WORKFLOW, domain=make_domain(values)).report
            self.assertEqual(validated_fields(report)["validated_result"], {"value": total})
            self.assertNotIn("queries", report)

    def test_missing_evidence_and_wrong_arithmetic_cannot_publish(self):
        for code in ('Total(value=6)', 'query(Read())\nTotal(value=7)'):
            report = run(code, domain=make_domain()).report
            self.assertIsNone(validated_fields(report)["validated_answer"])

    def test_invalid_batch_never_becomes_evidence(self):
        execution = run('try:\n    query(Read())\nexcept ValueError:\n    pass\nTotal(value=2)',
                        domain=make_domain((2, "invalid")))
        self.assertFalse(execution.evidence.completed(Read()))
        self.assertIsNone(validated_fields(execution.report)["validated_answer"])

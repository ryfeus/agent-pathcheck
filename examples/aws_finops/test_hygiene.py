import unittest
from unittest.mock import patch

from mcp import Client
from examples.aws_finops import policy as policy_module
from examples.aws_finops.benchmark import offline_benchmark
from examples.aws_finops.benchmark_cases import scenarios
from examples.aws_finops.hygiene import leak_problems, policy_spans
from examples.aws_finops.mcp_server import create_server
from agent_pathcheck import contracts


class HygieneTests(unittest.IsolatedAsyncioTestCase):
    async def test_descriptions(self):
        for case in ('costs', 'rightsizing'):
            for mode in policy_module.modes_for(case):
                async with Client(create_server(case=case, policy=mode)) as client:
                    for tool in (await client.list_tools()).tools:
                        text = tool.description
                        self.assertEqual(leak_problems(text, sources=[policy_module.build_policy(case, mode).contract],
                                                       allowed_spans=policy_spans(text)), [])

    def test_guest_validation_hints_for_benchmark_and_probes(self):
        hints = []
        original = contracts.validation_message
        def capture(*args):
            text = original(*args)
            hints.append(text)
            return text
        with patch.object(contracts, 'validation_message', capture):
            result = offline_benchmark(scenarios())
            self.assertEqual(result['matched'], 53)
            for row in result['rows']:
                hints.extend(row['report']['errors'])
                hints.extend(call['error'] for call in row['report']['policy_calls'] if 'error' in call)
            from examples.aws_finops.tracer import run_checked, COST_WORKFLOW, rightsizing_workflow
            run_checked(COST_WORKFLOW, case='costs')
            for mode in ('cpu-only', 'memory-required'):
                run_checked(rightsizing_workflow(mode), policy=mode)
            from examples.aws_finops.semantic_cases import semantic_scenarios, semantic_witness
            for scenario in semantic_scenarios().values():
                run_checked(semantic_witness(scenario), **scenario.host_options())
        self.assertTrue(hints)
        for text in hints:
            self.assertEqual(leak_problems(text, allowed_spans=policy_spans(text)), [], text)

    def test_bad_fixtures_and_occurrence_scoped_exceptions(self):
        for text in ('Expected 43000', 'Expected 43,000', 'Expected 430.00', 'Expected $430',
                     'Expected 0', 'Expected 1', 'at most 43000', 'Expected 450359962737049.90'):
            self.assertTrue(leak_problems(text))
        source = 'limit = 1\nmessage = "Expected 1"'
        self.assertTrue(leak_problems(source, sources=[source]))
        self.assertTrue(leak_problems('at most 1; expected total 1',
                                     allowed_spans=policy_spans('at most 1; expected total 1')))

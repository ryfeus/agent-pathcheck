"""Real MCP retrieval with scripted providers and adversarial benchmark grading."""

from copy import deepcopy
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock

from mcp import Client

from examples.agent_support import INSTRUCTIONS
from agent_pathcheck.integrations.agent import run_loop
from agent_pathcheck.presentation import replay_requirements_met
from examples.aws_finops.benchmark import grade_trial, offline_benchmark, summarize
from examples.aws_finops.lookup_benchmark import (
    grade_lookup, lookup_expectations, lookup_instructions, lookup_preflight, lookup_scenarios,
)
from examples.aws_finops.mcp_server import create_server
from examples.aws_finops.semantic_cases import semantic_witness


class LookupBenchmarkTests(unittest.IsolatedAsyncioTestCase):
    def response(self, provider, calls=(), text=''):
        if provider == 'openai':
            return NS(status='completed', usage=None, output_text=text,
                      output=[NS(type='function_call', name=n, arguments=json.dumps(a), call_id=str(i))
                              for i, (n, a) in enumerate(calls)])
        return NS(stop_reason='tool_use' if calls else 'end_turn', usage=None,
                  content=[NS(type='tool_use', name=n, input=a, id=str(i)) for i, (n, a) in enumerate(calls)]
                          + ([] if calls else [NS(type='text', text=text)]))

    async def execute(self, scenario, provider='openai', *, reads=None, late=False, code=None):
        expected = lookup_expectations(scenario)
        lookups = [('read_semantic', {'name': page['path']}) for page in expected['pages']]
        if reads is not None:
            lookups = [('read_semantic', {'name': name}) for name in reads]
        workflow = ('run_workflow', {'code': semantic_witness(scenario) if code is None else code})
        calls = [workflow, *lookups] if late else [*lookups, workflow]
        final = json.dumps({'validated_result': scenario.expected, 'semantic_notes': expected['semantic_notes']})
        turns = [self.response(provider, [call]) for call in calls] + [self.response(provider, text=final)]
        api = NS(**{('responses' if provider == 'openai' else 'messages'): NS(create=AsyncMock(side_effect=turns))})
        instructions = INSTRUCTIONS + '\n\n' + lookup_instructions(scenario)
        async with Client(create_server(**scenario.host_options(), replays=True)) as client:
            result = await run_loop(api, client, model='test', provider=provider, instructions=instructions)
        return result

    async def test_required_reads_and_metadata_pass_for_both_providers(self):
        for scenario in lookup_scenarios().values():
            for provider in ('openai', 'anthropic'):
                with self.subTest(scenario=scenario.name, provider=provider):
                    result = await self.execute(scenario, provider)
                    grade = grade_trial(scenario, result)
                    self.assertEqual(grade['outcome'], 'solved', grade)
                    self.assertTrue(grade['semantic_lookup']['passed'])
                    self.assertEqual(result['discovered_tools'], ['run_workflow', 'read_semantic'])
                    self.assertEqual([e['seq'] for e in result['tool_activity']], [1, 2, 3])
                    self.assertEqual([e['turn'] for e in result['tool_activity']], [1, 2, 3])
                    self.assertEqual(len(result['tool_calls']), 1)
                    self.assertEqual(len(result['lookups']), 2)
                    self.assertTrue(replay_requirements_met(result['last_report']))
                    self.assertEqual(result['final_text_status'], 'unverified')
                    # Lookup events are outside execution and never appear in its ledger trace.
                    self.assertFalse(any('read_semantic' in str(e) for e in result['last_report']['timeline']))

    async def test_correct_workflow_cannot_hide_skipped_wrong_failed_or_late_reads(self):
        scenario = lookup_scenarios()['lookup_missing_memory']
        for reads, late in [([], False), (['index', 'index'], False),
                            (['missing', '../secret'], False), (None, True)]:
            result = await self.execute(scenario, reads=reads, late=late)
            grade = grade_trial(scenario, result)
            self.assertEqual(result['validation_status'], 'passed')
            self.assertEqual(grade['workflow_outcome'], 'solved')
            self.assertEqual(grade['outcome'], 'semantic_lookup_failed')
            self.assertTrue(grade['semantic_lookup']['missing_before_workflow'])

    async def test_aliases_work_and_wrong_metadata_cannot_pass(self):
        scenario = lookup_scenarios()['lookup_min_savings']
        result = await self.execute(scenario, reads=list(scenario.required_rules))
        self.assertTrue(grade_lookup(scenario, result)['passed'])
        for field, value in [('level', 'documented'), ('source', 'invented'), ('why', 'A paraphrase')]:
            changed = deepcopy(result)
            final = json.loads(changed['final_text'])
            final['semantic_notes'][0][field] = value
            changed['final_text'] = json.dumps(final)
            self.assertFalse(grade_lookup(scenario, changed)['passed'])
        for final in ('not JSON', '{}', json.dumps({'validated_result': None, 'semantic_notes': []})):
            self.assertFalse(grade_lookup(scenario, {**result, 'final_text': final})['passed'])
        changed = deepcopy(result)
        changed['tool_activity'][0]['content_sha256'] = 'wrong-snapshot'
        self.assertFalse(grade_lookup(scenario, changed)['passed'])
        changed = deepcopy(result)
        # Sequential host execution of one model batch cannot demonstrate that
        # the model received the pages before submitting its workflow.
        for event in changed['tool_activity']:
            event['turn'] = 1
        self.assertFalse(grade_lookup(scenario, changed)['passed'])
        changed = deepcopy(result)
        changed['last_report']['context']['docs_hash'] = 'wrong-snapshot'
        self.assertFalse(grade_lookup(scenario, changed)['passed'])

    async def test_documentation_success_does_not_certify_a_failed_workflow(self):
        scenario = lookup_scenarios()['lookup_missing_memory']
        result = await self.execute(scenario, code='0')
        grade = grade_trial(scenario, result)
        self.assertEqual(grade['outcome'], 'agent_failed')
        self.assertIsNone(result['validated_answer'])

    async def test_preflight_and_summary_separate_lookup_from_workflow(self):
        cases = lookup_scenarios()
        preflight = await lookup_preflight(cases)
        self.assertEqual((preflight['matched'], preflight['checks']), (6, 6))
        offline = offline_benchmark(cases, replays=True)
        self.assertEqual((offline['matched'], offline['checks']), (3, 3))
        scenario = cases['lookup_missing_memory']
        result = await self.execute(scenario)
        row = dict(scenario=scenario.name, blocked_by=None, agent=result, grade=grade_trial(scenario, result))
        summary = summarize([row])
        self.assertEqual(summary['semantic_lookup_trials'], 1)
        self.assertEqual(summary['semantic_lookup_passes'], 1)
        self.assertEqual(summary['semantic_lookup_workflow_solves'], 1)
        self.assertEqual(summary['docs_lookups_per_trial'], [2])

    def test_task_names_pages_without_disclosing_expected_metadata(self):
        for scenario in lookup_scenarios().values():
            instructions = lookup_instructions(scenario)
            expected = lookup_expectations(scenario)
            for note in expected['semantic_notes']:
                self.assertIn('rules/' + note['rule_id'], instructions)
                self.assertNotIn(note['why'], instructions)
                self.assertNotIn(note['source'], instructions)

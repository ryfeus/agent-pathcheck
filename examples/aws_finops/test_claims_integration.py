"""Offline MCP, documentation, replay and independent benchmark integration."""

import json
import subprocess
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from mcp import Client

from agent_pathcheck import contracts
from agent_pathcheck.integrations.agent import run_loop
from examples.aws_finops.benchmark import grade_trial, offline_benchmark
from examples.aws_finops.claims_cases import CLAIMS_WITNESS, TOP_SERVICES_WITNESS, claims_scenarios, claim_witness
from examples.aws_finops.hygiene import leak_problems, policy_spans
from examples.aws_finops.mcp_server import create_server
from examples.aws_finops.policy import build_policy
from examples.aws_finops.semantic import check, claim_variants, directory_for
from examples.aws_finops.tracer import replays_for, run_checked


class ClaimsIntegrationTests(unittest.TestCase):
    def test_separate_benchmark_and_required_replays(self):
        for replays in (False, True):
            result = offline_benchmark(claims_scenarios(), replays=replays)
            self.assertEqual((result['checks'], result['matched']), (23, 23),
                             [(r['name'], r['report']['errors']) for r in result['rows'] if not r['matched']])

    def test_host_filled_adapts_and_supplied_constants_fail_replay(self):
        scenario = claims_scenarios()['claims_cost_basic']
        options = dict(**scenario.host_options(), replays=replays_for('cost-claims'), require_replays=True)
        dynamic = run_checked(CLAIMS_WITNESS, **options)
        self.assertEqual(dynamic['validation_status'], 'passed')
        self.assertEqual(dynamic['replays'][0]['verdict'], 'passed')
        value = dynamic['result']['claims']['delta']['before']['value']
        fixed = run_checked(CLAIMS_WITNESS.replace('CostTotal(source=before)',
                            f'CostTotal(source=before,value={value})'), **options)
        self.assertEqual(fixed['contract_check'], 'passed')
        self.assertEqual(fixed['replays'][0]['verdict'], 'failed')
        self.assertIsNone(fixed['validated_answer'])
        self.assertNotIn('answer_presentation', fixed)
        empty = claims_scenarios()['claims_cost_empty_both']
        report = run_checked(CLAIMS_WITNESS, **empty.host_options(),
                             replays=replays_for('cost-claims'), require_replays=True)
        self.assertEqual(report['validation_status'], 'passed')
        self.assertEqual(report['replays'][0]['verdict'], 'inconclusive')
        self.assertTrue(report['replays'][0]['exemption_reason'])

    def test_grader_accepts_keys_prose_and_extra_claims(self):
        scenario = claims_scenarios()['claims_cost_basic']
        code = CLAIMS_WITNESS.replace('"delta": delta', '"change": delta, "total": CostTotal(source=after)').replace(
            'Total change: {delta}.', 'Verified change: {change}.')
        report = run_checked(code, **scenario.host_options())
        self.assertEqual(report['answer_check'], 'passed')
        trial = {'status': 'completed', **report, 'tool_calls': [{'output': report}]}
        self.assertEqual(grade_trial(scenario, trial)['outcome'], 'solved')

    def test_committed_semantic_trees_and_demo(self):
        for case, mode, question in claim_variants():
            self.assertEqual(check(case, mode, question=question), [])
            policy = build_policy(case, mode, question=question)
            index = (directory_for(case, mode, question) / 'index.md').read_text()
            for spec in policy.parameters['required_claims']:
                self.assertIn(spec.description(), index)
        for question in ('comparison', 'top-services'):
            command = [sys.executable, '-m', 'examples.aws_finops.mcp_server', '--demo',
                       '--case', 'cost-claims', '--question', question]
            report = json.loads(subprocess.check_output(command, text=True))
            self.assertEqual(report['answer_check'], 'passed')
            self.assertEqual(report['validated_answer']['text'], report['answer_presentation']['text'])

    def test_second_question_reuses_output_schema_and_handles_unchanged_replay(self):
        comparison = build_policy('cost-claims', 'memory-required')
        top = build_policy('cost-claims', 'memory-required', question='top-services')
        self.assertEqual(comparison.output_model.model_json_schema(), top.output_model.model_json_schema())
        self.assertNotEqual(comparison.policy_hash, top.policy_hash)
        for fixture in ('base', 'variant'):
            report = run_checked(TOP_SERVICES_WITNESS, case='cost-claims', question='top-services', fixture=fixture)
            self.assertEqual(report['answer_check'], 'passed')
        from examples.aws_finops.benchmark_cases import cost_scenario
        # The arithmetic perturbation changes a before-only service outside the selected prefix.
        old = cost_scenario('unselected', {'Old': (100, None), 'A': (0, 30), 'B': (0, 20), 'C': (0, 10)}, '')
        report = run_checked(TOP_SERVICES_WITNESS, case='cost-claims', question='top-services', fixture_sql=old.sql,
                             expected_result=dict(current_total_cents=60, services=old.expected['services'][:3]),
                             replays=replays_for('cost-claims'), require_replays=True)
        self.assertEqual(report['validation_status'], 'passed', report['errors'])
        self.assertEqual(report['replays'][0]['verdict'], 'inconclusive')
        self.assertTrue(report['replays'][0]['exemption_reason'])

    def test_all_guest_error_hints_are_oracle_free(self):
        hints = []
        original = contracts.validation_message
        def capture(*args):
            text = original(*args)
            hints.append(text)
            return text
        with patch.object(contracts, 'validation_message', capture):
            result = offline_benchmark(claims_scenarios(), replays=True)
        for row in result['rows']:
            hints.extend(row['report']['errors'])
            hints.extend(call['error'] for call in row['report']['policy_calls'] if 'error' in call)
        self.assertTrue(hints)
        for text in hints:
            self.assertEqual(leak_problems(text, allowed_spans=policy_spans(text)), [], text)


class ClaimsMCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_lookup_and_host_presentation(self):
        for case, mode, question in claim_variants():
            policy = build_policy(case, mode, question=question)
            async with Client(create_server(case=case, policy=mode, question=question)) as client:
                tools = (await client.list_tools()).tools
                self.assertEqual({t.name for t in tools}, {'run_workflow', 'read_semantic'})
                for tool in tools:
                    text = tool.description
                    self.assertEqual(leak_problems(text, sources=[policy.contract],
                                                   allowed_spans=policy_spans(text)), [])
                self.assertIn('max_claim_rejections: 0', tools[0].description)
                page = await client.call_tool('read_semantic', {'name': 'claims.required_missing'})
                self.assertFalse(page.is_error)
                reply = await client.call_tool('run_workflow', {'code': claim_witness(question)})
                report = reply.structured_content
                self.assertEqual(report['validation_status'], 'passed', report['errors'])
                self.assertTrue(report['validated_answer']['claims'])
                self.assertIn('docs_hash', report['context'])

    async def test_provider_loop_keeps_rendered_answer_after_lookup(self):
        def response(calls):
            return NS(status='completed', usage=None, output_text='Done', output=[
                NS(type='function_call', name=name, arguments=json.dumps(args), call_id=str(i))
                for i, (name, args) in enumerate(calls)])
        api = NS(responses=NS(create=AsyncMock(side_effect=[
            response([('run_workflow', {'code': CLAIMS_WITNESS})]),
            response([('read_semantic', {'name': 'CostTotal'})]), response([])])))
        async with Client(create_server(case='cost-claims')) as client:
            result = await run_loop(api, client, model='offline-test', instructions='Solve')
        self.assertEqual(result['validation_status'], 'passed')
        self.assertEqual(result['validated_answer'], result['last_report']['validated_answer'])
        self.assertIn('text', result['validated_answer'])

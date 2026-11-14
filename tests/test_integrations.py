"""Both provider wire adapters preserve the same execution boundary."""

import json
from types import SimpleNamespace as NS
from pathlib import Path
import unittest
from unittest.mock import AsyncMock

from mcp import Client
from pydantic import Field, ValidationError
from sqlalchemy import Column, Integer, String, Table, MetaData
from typing import Literal

from agent_pathcheck.extensions.sqlalchemy import request_model, result_model
from agent_pathcheck.integrations.agent import run_loop
from examples.aws_finops.policy import Candidate, Evidence
from examples.aws_finops.queries import candidates_statement, evidence_statement
from examples.sql_math_tracer.mcp_server import create_server
from examples.sql_math_tracer.policy import REFERENCE_WORKFLOWS
from examples.sql_math_tracer.tracer import BASE_TASKS


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    def response(self, provider, calls=(), text="Unverified prose", complete=True):
        if provider == 'openai':
            return NS(status='completed' if complete else 'incomplete', usage=None, output_text=text,
                      output=[NS(type='function_call', name=name, arguments=json.dumps(args), call_id=f'c{i}')
                              for i, (name, args) in enumerate(calls)])
        return NS(stop_reason=('tool_use' if calls else 'end_turn') if complete else 'max_tokens', usage=None,
                  content=[NS(type='tool_use', name=name, input=args, id=f'c{i}') for i, (name, args) in enumerate(calls)]
                          + ([] if calls else [NS(type='text', text=text)]))

    async def loop(self, provider, turns, **limits):
        api = NS(**{('responses' if provider == 'openai' else 'messages'): NS(create=AsyncMock(side_effect=turns))})
        async with Client(create_server(BASE_TASKS['margin'])) as mcp:
            result = await run_loop(api, mcp, model='test', instructions='Solve', provider=provider,
                                    tool_name='run_workflow', **limits)
        return result, api

    async def test_repair_and_exact_schema_for_each_provider(self):
        good = REFERENCE_WORKFLOWS['totals']['margin']
        for provider in ('openai', 'anthropic'):
            with self.subTest(provider=provider):
                result, api = await self.loop(provider, [
                    self.response(provider, [('run_workflow', {'code': '0'})]),
                    self.response(provider, [('run_workflow', {'code': good})]), self.response(provider)])
                self.assertEqual(result['validation_status'], 'passed')
                self.assertEqual(result['validated_result'], 0.324)
                self.assertEqual(result['final_text_status'], 'unverified')
                request = getattr(api, 'responses' if provider == 'openai' else 'messages').create.call_args.kwargs
                tool = request['tools'][0]
                schema = tool['parameters' if provider == 'openai' else 'input_schema']
                self.assertEqual(set(schema['properties']), {'code'})
                self.assertFalse(schema['additionalProperties'])

    async def test_new_invalid_attempt_clears_success_and_limits_are_shared(self):
        good = ('run_workflow', {'code': REFERENCE_WORKFLOWS['totals']['margin']})
        for provider in ('openai', 'anthropic'):
            for bad, limits in [(('unknown', {}), {}), (('run_workflow', {'code': '0', 'extra': True}), {}),
                                (('run_workflow', {'code': '0'}), {'max_tool_calls': 1})]:
                with self.subTest(provider=provider, bad=bad, limits=limits):
                    result, _ = await self.loop(provider, [self.response(provider, [good]),
                                                          self.response(provider, [bad]), self.response(provider)], **limits)
                    self.assertIsNone(result['validated_answer'])
                    self.assertIsNone(result['last_report'])
            result, _ = await self.loop(provider, [self.response(provider, [('unknown', {})])], max_turns=1)
            self.assertEqual(result['status'], 'turn_limit')
            result, _ = await self.loop(provider, [self.response(provider, complete=False)])
            self.assertEqual(result['status'], 'model_incomplete')
            result, _ = await self.loop(provider, [self.response(provider, [good]),
                                                  self.response(provider, [('run_workflow', {'code': '0'})], complete=False)])
            self.assertEqual(result['status'], 'model_incomplete')
            self.assertIsNone(result['validated_answer'])
            result, _ = await self.loop(provider, [RuntimeError('private credential must not leak')])
            self.assertEqual(result['status'], 'api_error')
            self.assertNotIn('credential', json.dumps(result))

    async def test_anthropic_multiple_tool_results_use_one_user_message(self):
        good = REFERENCE_WORKFLOWS['totals']['margin']
        result, api = await self.loop('anthropic', [
            self.response('anthropic', [('run_workflow', {'code': '0'}), ('run_workflow', {'code': good})]),
            self.response('anthropic')])
        self.assertEqual(result['validation_status'], 'passed')
        messages = api.messages.create.call_args.kwargs['messages']
        results = [m for m in messages if m['role'] == 'user' and isinstance(m['content'], list)]
        self.assertEqual(len(results), 1)
        self.assertEqual([b['tool_use_id'] for b in results[0]['content']], ['c0', 'c1'])


class SQLAlchemyTests(unittest.TestCase):
    def test_generated_domain_models_match_handwritten_schemas(self):
        # Captured from the handwritten models before replacing them with generation.
        expected = json.loads((Path(__file__).with_name('fixtures') / 'finops_schemas.json').read_text())
        for model in (Candidate, Evidence):
            self.assertEqual(model.model_json_schema(), expected[model.__name__])

    def test_nullability_scope_and_strictness(self):
        table = Table('sample', MetaData(), Column('account', String, nullable=False), Column('value', Integer))
        row = result_model('Row', table.c)
        self.assertIsNone(row(account='prod', value=None).value)
        with self.assertRaises(ValidationError):
            row(account='prod', value='1')
        request = request_model('Read', [table.c.account], scope={'account': 'prod'})
        request(account='prod')
        with self.assertRaises(ValidationError):
            request(account='dev')
        with self.assertRaises(ValueError):
            request_model('Read', [table.c.account], scope={})

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock

from mcp import Client
from agent_pathcheck.integrations.agent import run_loop
from agent_pathcheck.semantic import SemanticReader
from examples.aws_finops.semantic import ROOT
from examples.aws_finops.mcp_server import create_server
from examples.aws_finops.tracer import rightsizing_workflow


class LookupTests(unittest.IsolatedAsyncioTestCase):
    def response(self, provider, calls=(), complete=True):
        if provider == 'openai':
            return NS(status='completed' if complete else 'incomplete', usage=None, output_text='Done',
                      output=[NS(type='function_call',name=n,arguments=json.dumps(a),call_id=str(i))
                              for i,(n,a) in enumerate(calls)])
        return NS(stop_reason=('tool_use' if calls else 'end_turn') if complete else 'max_tokens',usage=None,
                  content=[NS(type='tool_use',name=n,input=a,id=str(i)) for i,(n,a) in enumerate(calls)])

    async def loop(self, provider, turns, **options):
        api=NS(**{('responses' if provider=='openai' else 'messages'):NS(create=AsyncMock(side_effect=turns))})
        async with Client(create_server()) as client:
            return await run_loop(api,client,model='test',instructions='Solve',provider=provider,**options)

    async def test_lookup_preserves_reports_for_both_providers(self):
        lookup=('read_semantic',{'name':'Candidate'})
        for provider in ('openai','anthropic'):
            for code, expected in [(rightsizing_workflow(),'passed'),('0','failed')]:
                r=await self.loop(provider,[self.response(provider,[('run_workflow',{'code':code}),lookup]),
                                            self.response(provider,[lookup]),self.response(provider)])
                self.assertEqual(r['validation_status'],expected)
                self.assertEqual(len(r['tool_calls']),1)
                self.assertEqual(r['lookups'],[{'name':'Candidate','ok':True}]*2)
                self.assertIn('docs_hash',r['last_report']['context'])
            r=await self.loop(provider,[self.response(provider,[lookup]),self.response(provider)])
            self.assertIsNone(r['validated_answer'])

    async def test_lookup_budget_and_bad_names_do_not_clear_pass(self):
        for provider in ('openai','anthropic'):
            turns=[self.response(provider,[('run_workflow',{'code':rightsizing_workflow()})]),
                   self.response(provider,[('read_semantic',{'name':'../secret'})]),
                   self.response(provider,[('read_semantic',{'name':'Candidate'})])]
            r=await self.loop(provider,turns,max_lookups=1)
            self.assertEqual(r['status'],'lookup_limit')
            self.assertEqual(r['validation_status'],'passed')
            self.assertFalse(r['lookups'][0]['ok'])

    def test_snapshot_hash_and_traversal(self):
        reader=SemanticReader(ROOT/'rightsizing'/'memory-required')
        self.assertEqual(reader.read('Candidate'),reader.read('entities/candidate'))
        for name in ('../index','/index','entities/../../index','entities\\candidate','missing'):
            with self.assertRaises(ValueError): reader.read(name)
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'index.md';p.write_text(reader.read('index'))
            old=SemanticReader(d);p.write_text(p.read_text()+'Changed authored prose.\n')
            self.assertNotEqual(old.docs_hash,SemanticReader(d).docs_hash)
            self.assertNotEqual(old.read('index'),p.read_text())
            (Path(d)/'escape.md').symlink_to(ROOT/'rightsizing'/'memory-required'/'index.md')
            with self.assertRaises(ValueError): SemanticReader(d)

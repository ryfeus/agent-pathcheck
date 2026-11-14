from dataclasses import replace
import unittest

from agent_pathcheck import Caveat, Evidence, Operation, PolicyModel, bind_operations
from agent_pathcheck.evidence import evidence_context


class AnnotationTests(unittest.TestCase):
    def test_annotations_commit_atomically_and_are_copied(self):
        class Read(PolicyModel):
            name: str
        class Row(PolicyModel):
            value: int
        ledger = Evidence()
        request = Read(name='sample')
        op = Operation(Read, Row, lambda _: [{'value':1}],
                       annotate=lambda request, rows: [Caveat(rule_id='sample.rule', text='A caveat')])
        with evidence_context(ledger,None):
            bind_operations([op],[],[])(request)
            self.assertEqual(ledger.all_caveats(), ledger.caveats(request))
            copied=ledger.caveats(request); copied.clear()
            self.assertEqual(len(ledger.caveats(request)),1)
            bad=replace(op, annotate=lambda request, rows: [{'rule_id':'bad'}])
            other=Read(name='failed')
            with self.assertRaisesRegex(RuntimeError,'annotation failed'):
                bind_operations([bad],[],[])(other)
            self.assertFalse(ledger.completed(other))
            self.assertEqual(len(ledger.all_caveats()),1)
            bind_operations([replace(op, annotate=lambda request,rows: [])],[],[])(request)
            self.assertEqual(ledger.caveats(request),[])
            self.assertEqual(ledger.all_caveats(),[])

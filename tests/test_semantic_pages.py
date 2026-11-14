from dataclasses import replace
from datetime import date
from pathlib import Path
import tempfile
import unittest
import warnings

from agent_pathcheck.semantic import build_pages, write_pages, check_pages, page_parts, docs_hash
from examples.aws_finops.semantic import configuration, ROOT, check, variants, hygiene


class PageTests(unittest.TestCase):
    def test_committed_trees_and_roundtrip(self):
        for case, mode in variants():
            self.assertEqual(check(case, mode), [])
        args = configuration('costs', 'memory-required')
        with tempfile.TemporaryDirectory() as d:
            pages = build_pages(*args)
            write_pages(d, pages)
            p = Path(d)/'index.md'
            p.write_text(p.read_text().replace('## Meaning\n', '## Meaning\n\nA retained authored paragraph.'))
            authored = page_parts(p.read_text())[1]
            before = docs_hash({n: (Path(d)/n).read_text() for n in pages})
            write_pages(d, pages)
            self.assertEqual(page_parts(p.read_text())[1], authored)
            p.write_text(p.read_text()+'More authored prose.\n')
            self.assertNotEqual(before, docs_hash({n: (Path(d)/n).read_text() for n in pages}))

    def test_checker_failure_modes(self):
        args = configuration('costs', 'memory-required')
        original = {p.relative_to(ROOT/'costs'/'memory-required').as_posix(): p.read_text()
                    for p in (ROOT/'costs'/'memory-required').rglob('*.md')}
        changes = [lambda t:t.replace('anchor:', 'missing:'), lambda t:t.replace('anchor:', 'anchor: "unknown"\nignored:'),
                   lambda t:t.replace('Semantic map', 'Stale title'), lambda t:t.replace('## Meaning', '## Extra'),
                   lambda t:t.replace('rules/costs.total.md', 'missing.md'),
                   lambda t:t.replace('## Rationale', '## Removed'),
                   lambda t:t.replace('<!-- generated:end -->', ''),
                   lambda t:t+'\nExpected total 43000\n',
                   lambda t:t+'\n```python\nanswer = 43000\n```\n',
                   lambda t:t+'\nField `answer`: 43000\n']
        with tempfile.TemporaryDirectory() as d:
            for change in changes:
                for name,text in original.items():
                    p=Path(d)/name; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(text)
                (Path(d)/'index.md').write_text(change(original['index.md']))
                self.assertTrue(check_pages(d,*args,hygiene=hygiene))
            (Path(d)/'index.md').unlink()
            self.assertTrue(any('no page' in x for x in check_pages(d,*args)))
            (Path(d)/'extra.md').write_text(original['glossary.md'])
            self.assertTrue(any('unknown anchor' in x or 'duplicate anchor' in x for x in check_pages(d,*args)))

    def test_overdue_review_warns_without_failure(self):
        policy, *rest = configuration('costs', 'memory-required')
        rules = dict(policy.rules)
        rules['costs.total'] = replace(rules['costs.total'], review_by='2000-01-01')
        policy = replace(policy, rules=rules)
        with tempfile.TemporaryDirectory() as d:
            for p in (ROOT/'costs'/'memory-required').rglob('*.md'):
                target=Path(d)/p.relative_to(ROOT/'costs'/'memory-required')
                target.parent.mkdir(parents=True,exist_ok=True); target.write_text(p.read_text())
            write_pages(d,build_pages(policy,*rest))
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter('always')
                self.assertEqual(check_pages(d,policy,*rest,today=date(2026,11,9)),[])
            self.assertTrue(caught)

from dataclasses import replace
import unittest

from agent_pathcheck.semantic import check_declarations
from examples.aws_finops.policy import build_policy, operations_for
from examples.aws_finops.queries import metadata
from examples.aws_finops.semantic_config import declarations


class DeclarationTests(unittest.TestCase):
    def test_real_fields_and_literal_scopes(self):
        for case in ('costs', 'rightsizing'):
            for mode in ('cpu-only', 'memory-required'):
                policy = build_policy(case, mode)
                links, words = declarations(policy)
                models = list(policy.parameters['models'].values())
                models += [op.result_type for op in operations_for(policy.parameters['models'], None)]
                self.assertEqual(check_declarations(links, words, models, metadata.tables), [])
                self.assertTrue(check_declarations([replace(links[0], source_field='invented')], words, models, metadata.tables))
                self.assertTrue(check_declarations(links, [replace(words[0], value='invented')], models, metadata.tables))
                self.assertTrue(check_declarations([replace(links[0], target='invented')], words, models, metadata.tables))

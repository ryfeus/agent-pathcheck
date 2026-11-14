"""In-memory contracts exercise claims independently of FinOps and SQL."""

from contextlib import contextmanager
from typing import Literal
import unittest

from pydantic import ValidationError, model_validator

from agent_pathcheck import (Domain, Invocation, Operation, Policy, PolicyModel,
                             bind_operations, current_evidence, run, validate_model_output)
from agent_pathcheck.evidence import Evidence, evidence_context
from agent_pathcheck.extensions.claims import Claim, RequiredClaim, answer_model, render_answer, settle


class Read(PolicyModel):
    scope: Literal['first', 'second']


class Row(PolicyModel):
    amount: int


class Total(Claim):
    source: Read
    value: int | None = None

    @model_validator(mode='before')
    @classmethod
    def check(cls, data):
        data = data.model_dump() if isinstance(data, cls) else data
        if not isinstance(data, dict) or 'source' not in data:
            return data
        source = Read.model_validate(data['source'])
        return settle(data, sum(row.amount for row in current_evidence().require(source)), 'claims.total')


class Delta(Claim):
    before: Total
    after: Total
    value: int | None = None

    @model_validator(mode='before')
    @classmethod
    def check(cls, data):
        data = data.model_dump() if isinstance(data, cls) else data
        if not isinstance(data, dict) or not {'before', 'after'} <= data.keys():
            return data
        before, after = Total.model_validate(data['before']), Total.model_validate(data['after'])
        return settle(data, after.value - before.value, 'claims.delta')


def domain_for(required=(), limit=None):
    Answer = answer_model((Total, Delta), required=required)

    @contextmanager
    def invocation(report):
        query = bind_operations([Operation(Read, Row, lambda q: [{'amount': 7 if q.scope == 'first' else 12}])],
                                report['policy_calls'], report['policy_events'])
        yield Invocation({}, {'query': query}, {'Read': Read, 'Total': Total, 'Alias': Total,
                         'Delta': Delta, 'Answer': Answer},
                         lambda v: validate_model_output(v, Answer, report['policy_events']),
                         frozenset({'Total', 'Delta', 'Answer'}))

    policy = Policy('claims-test', '1', invocation, Answer, {'request_types': (Read,)})
    return Domain({}, invocation, policy=policy, max_claim_rejections=limit)


PREFIX = 'q = Read(scope="first")\nquery(q)\n'
ANSWER = 'Answer(claims={"total": Total(source=q)}, text="Observed {total}.")'


class ComposableClaimTests(unittest.TestCase):
    def test_fill_check_and_strict_types(self):
        for expression in (ANSWER, ANSWER.replace('source=q)', 'source=q, value=7)'),
                           'Answer(claims={"total": {"source": q}}, text="{total}")'):
            report = run(PREFIX + expression, domain=domain_for()).report
            self.assertEqual(report['contract_check'], 'passed', report['errors'])
            self.assertEqual(report['result']['claims']['total']['value'], 7)
        for value, rule in [('8', 'claims.total'), ('7.0', 'pydantic.int_type')]:
            report = run(PREFIX + ANSWER.replace('source=q)', f'source=q, value={value})'), domain=domain_for()).report
            self.assertEqual(report['failure']['rule_id'], rule)

    def test_prerequisite_scope_and_forged_nested_claim(self):
        programs = [
            ('q = Read(scope="first")\n' + ANSWER, 'evidence.required'),
            ('Read(scope="outside")', 'pydantic.literal_error'),
            (PREFIX + 'Delta(before={"source": q, "value": 8}, after={"source": q})', 'claims.total'),
        ]
        for code, rule in programs:
            self.assertEqual(run(code, domain=domain_for()).report['failure']['rule_id'], rule)

    def test_text_rules(self):
        for text in ('{missing}', '{total.value}', '{total[0]}', '{total:03}', '{total!r}',
                     '{', '}', '{{total}}', '{}', '{total:}', '{0}'):
            report = run(PREFIX + f'Answer(claims={{"total":Total(source=q)}},text={text!r})',
                         domain=domain_for()).report
            self.assertEqual(report['failure']['rule_id'], 'claims.text_placeholder', text)
        for text in ('Observed 7', 'Observed ７', 'Observed ²'):
            report = run(PREFIX + f'Answer(claims={{"total":Total(source=q)}},text={text!r})',
                         domain=domain_for()).report
            self.assertEqual(report['failure']['rule_id'], 'claims.text_numbers')
        report = run('Answer(claims={}, text="Empty")', domain=domain_for()).report
        self.assertEqual(report['failure']['rule_id'], 'claims.empty')

    def test_required_sources_and_extra_claims(self):
        required = [RequiredClaim(Total, {'source': {'scope': 'first'}})]
        domain = domain_for(required)
        good = run(PREFIX + ANSWER, domain=domain).report
        self.assertEqual(good['contract_check'], 'passed')
        wrong = run((PREFIX + ANSWER).replace('first', 'second'), domain=domain).report
        self.assertEqual(wrong['failure']['rule_id'], 'claims.required_missing')
        code = PREFIX + 's = Read(scope="second")\nquery(s)\n' + ANSWER.replace(
            '"total": Total(source=q)', '"total": Total(source=q), "extra": Total(source=s)')
        self.assertEqual(run(code, domain=domain).report['contract_check'], 'passed')

    def test_rejections_latch_and_legacy_default(self):
        guesses = PREFIX + 'for value in [6, 7]:\n    try:\n        Alias(source=q, value=value)\n    except ValueError:\n        pass\n' + ANSWER
        limited = run(guesses, domain=domain_for(limit=0)).report
        self.assertEqual(limited['failure']['rule_id'], 'claims.rejection_limit')
        self.assertIsNone(limited['result'])
        self.assertEqual(run(guesses, domain=domain_for()).report['contract_check'], 'passed')
        code = PREFIX + 'try:\n    Answer(claims={},text="Empty")\nexcept ValueError:\n    pass\n' + ANSWER
        self.assertEqual(run(code, domain=domain_for(limit=0)).report['failure']['rule_id'], 'claims.rejection_limit')
        self.assertEqual(run(PREFIX + ANSWER, domain=domain_for(limit=0)).report['contract_check'], 'passed')

    def test_render_uses_checked_values_and_scope(self):
        domain = domain_for()
        ledger = Evidence()
        ledger._commit(Read(scope='first'), [Row(amount=7)])
        with evidence_context(ledger, domain.policy):
            total = Total(source=Read(scope='first'))
            answer = domain.policy.output_model(claims={'total': total}, text='Observed {total}.')
            rendered = render_answer(answer, {Total: lambda value: f'checked:{value}'})
            self.assertEqual(rendered['text'], 'Observed checked:7.')
            self.assertEqual(rendered['claims'], [dict(name='total', type='Total',
                              scope={'source': {'scope': 'first'}}, qualifiers={}, value=7)])
            self.assertEqual(settle(total, 7, 'claims.total'), total)
            with self.assertRaises(ValueError):
                settle(total, 8, 'claims.total')

    def test_raw_dict_and_bad_configuration(self):
        report = run('{"claims": {}, "text": "Forged"}', domain=domain_for()).report
        self.assertEqual(report['failure']['class'], 'contract')
        for limit in (-1, True, 1.0):
            with self.assertRaises(ValueError):
                domain_for(limit=limit)
        with self.assertRaises(ValueError):
            answer_model(())
        with self.assertRaises(ValueError):
            answer_model((Total,), required=[RequiredClaim(Delta, {})])

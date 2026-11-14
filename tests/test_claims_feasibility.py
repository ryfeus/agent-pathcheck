"""M0: before validators fill frozen models through Monty's constructor bridge.

An after validator returning a copy is ignored by Pydantic __init__, which
TracedType uses. Fill before validation instead; instance revalidation remains
enabled so nested models cannot retain forged or stale evidence-derived values.
"""

from contextlib import contextmanager
from typing import Literal
import unittest
import warnings

from pydantic import ValidationError, model_validator

from agent_pathcheck import (Domain, Invocation, Operation, Policy, PolicyModel,
                             PolicyViolation, bind_operations, current_evidence,
                             run, validate_model_output)
from agent_pathcheck.contracts import TracedType
from agent_pathcheck.evidence import Evidence, evidence_context


class Read(PolicyModel):
    scope: Literal['allowed']


class Row(PolicyModel):
    amount: int


class BeforeTotal(PolicyModel):
    source: Read
    value: int | None = None

    @model_validator(mode='before')
    @classmethod
    def fill_or_check(cls, data):
        data = data.model_dump() if isinstance(data, cls) else dict(data)
        source = Read.model_validate(data['source'])
        actual = sum(row.amount for row in current_evidence().require(source))
        if data.get('value') is None:
            data['value'] = actual
        elif data['value'] != actual:
            raise PolicyViolation('arithmetic', 'feasibility.total', 'Total must match observed evidence')
        return data


class Nested(PolicyModel):
    total: BeforeTotal


class AfterTotal(PolicyModel):
    value: int | None = None

    @model_validator(mode='after')
    def fill_copy(self):
        return self.model_copy(update={'value': 7}) if self.value is None else self


@contextmanager
def invocation(report):
    query = bind_operations([Operation(Read, Row, lambda _: [{'amount': 7}])],
                            report['policy_calls'], report['policy_events'])
    yield Invocation({}, {'query': query}, {'Read': Read, 'Total': BeforeTotal, 'Nested': Nested},
                     lambda value: validate_model_output(value, Nested, report['policy_events']))


class ClaimsFeasibilityTests(unittest.TestCase):
    def test_after_copy_is_not_supported_by_constructor(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            value = TracedType(AfterTotal, []).construct((), {}).value
        self.assertIsNone(value.value)
        self.assertTrue(any('Returning anything other than `self`' in str(w.message) for w in caught))

    def test_guest_fill_and_final_revalidation(self):
        policy = Policy('feasibility', '1', invocation, Nested)
        domain = Domain({}, invocation, policy=policy)
        prefix = 'q = Read(scope="allowed")\nquery(q)\n'
        for expression in ('Nested(total=Total(source=q))',
                           'Nested(total={"source": {"scope": "allowed"}})',
                           'Nested(total={"source": q, "value": 7})'):
            with self.subTest(expression=expression):
                report = run(prefix + expression, domain=domain).report
                self.assertEqual(report['contract_check'], 'passed', report['errors'])
                self.assertEqual(report['result']['total']['value'], 7)
        report = run(prefix + 'Nested(total={"source": q, "value": 8})', domain=domain).report
        self.assertEqual(report['failure']['rule_id'], 'feasibility.total')

    def test_nested_instances_and_dicts_revalidate_current_evidence(self):
        request, ledger = Read(scope='allowed'), Evidence()
        ledger._commit(request, [Row(amount=7)])
        with evidence_context(ledger, Policy('feasibility', '1', invocation, Nested)):
            valid = BeforeTotal(source=request)
            for forged in (valid.model_copy(update={'value': 8}),
                           {'source': request, 'value': 8}):
                with self.assertRaises(ValidationError):
                    Nested(total=forged)
            ledger._commit(request, [Row(amount=9)])
            with self.assertRaises(ValidationError):
                Nested(total=valid)
            with self.assertRaises(ValueError):
                validate_model_output(valid, BeforeTotal, [])

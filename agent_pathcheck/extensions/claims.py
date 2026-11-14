"""Optional, evidence-backed answer claims and host-owned presentation."""

from dataclasses import dataclass, field
import json
import re
from typing import ClassVar, Union

from pydantic import create_model, model_validator

from ..contracts import PolicyModel
from ..policy import current_policy
from ..violations import PolicyViolation


class Claim(PolicyModel):
    """Domain validators compute value (or rows); sources retain request scope."""

    def scope(self):
        requests = current_policy().parameters.get('request_types', ())
        result = {}
        for name in type(self).model_fields:
            value = getattr(self, name)
            if isinstance(value, Claim):
                result[name] = value.scope()
            elif type(value) in requests:
                result[name] = value.model_dump(mode='json')
        return result

    def qualifiers(self):
        sources = self.scope()
        return {name: value for name, value in self.model_dump(mode='json').items()
                if name not in sources and name not in ('value', 'rows')}

    def result(self):
        return self.model_dump(mode='json')['value' if 'value' in type(self).model_fields else 'rows']


def settle(claim, actual, rule_id, *, field='value'):
    """Fill an input mapping before validation, or copy an already typed claim.

    Supplied values stay intact for subsequent strict field validation; equality
    alone must not turn a boolean or floating-point number into a valid integer.
    """
    supplied = claim.get(field) if isinstance(claim, dict) else getattr(claim, field)
    if supplied is not None and supplied != actual:
        raise PolicyViolation('arithmetic', rule_id, f'{rule_id}: supplied result must match observed evidence')
    value = actual if supplied is None else supplied
    return ({**claim, field: value} if isinstance(claim, dict)
            else claim.model_copy(update={field: value}))


@dataclass(frozen=True)
class RequiredClaim:
    """Host-only source data and qualifiers; never instantiate a claim to specify it."""

    claim_type: type[Claim]
    sources: dict
    qualifiers: dict = field(default_factory=dict)

    def matches(self, claim):
        return (type(claim) is self.claim_type and claim.scope() == self.sources
                and claim.qualifiers() == self.qualifiers)

    def description(self):
        scope = json.dumps({**self.sources, **self.qualifiers}, sort_keys=True, separators=(',', ':'))
        return f'{self.claim_type.__name__}({scope})'


PLACEHOLDER = re.compile(r'\{([A-Za-z_][A-Za-z_0-9]*)\}')


class AnswerEnvelope(PolicyModel):
    claims: dict[str, Claim]
    text: str
    requirements: ClassVar[tuple] = ()

    @model_validator(mode='after')
    def check_answer(self):
        if not self.claims:
            raise PolicyViolation('completeness', 'claims.empty', 'Include at least one verified answer claim')
        names = PLACEHOLDER.findall(self.text)
        remaining = PLACEHOLDER.sub('', self.text)
        if '{' in remaining or '}' in remaining or any(name not in self.claims for name in names):
            raise PolicyViolation('schema', 'claims.text_placeholder',
                                  'Use only simple placeholders naming supplied claims')
        if any(char.isdigit() for char in remaining):
            raise PolicyViolation('schema', 'claims.text_numbers', 'Put numbers in verified claims, not prose')
        if any(not any(spec.matches(claim) for claim in self.claims.values()) for spec in self.requirements):
            raise PolicyViolation('completeness', 'claims.required_missing',
                                  'Include every required claim with its declared sources and qualifiers')
        return self


def answer_model(claim_types, *, required=None):
    types = tuple(claim_types)
    if not types or any(not issubclass(model, Claim) for model in types):
        raise ValueError('Answer types must be a nonempty collection of Claim subclasses')
    requirements = tuple(required or ())
    if any(spec.claim_type not in types for spec in requirements):
        raise ValueError('Required claim types must belong to this answer')
    model = create_model('Answer', __base__=AnswerEnvelope, claims=(dict[str, Union[types]], ...))
    model.requirements = requirements
    return model


def render_answer(answer, formatters):
    """Call only after final validation, with the policy's request registry bound."""
    rendered, substitutions = [], {}
    for name, claim in answer.claims.items():
        value = claim.result()
        substitutions[name] = formatters[type(claim)](value)
        rendered.append(dict(name=name, type=type(claim).__name__, scope=claim.scope(),
                             qualifiers=claim.qualifiers(), value=value))
    return dict(claims=rendered, text=PLACEHOLDER.sub(lambda match: substitutions[match[1]], answer.text))

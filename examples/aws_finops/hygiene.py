"""Host-only oracle leak checks with occurrence-scoped provenance exceptions."""

import io
from decimal import Decimal
from functools import lru_cache
import re
import tokenize


def oracle_values():
    from .tracer import COST_EXPECTED, CURRENT_COST_EXPECTED, PLAN_EXPECTED
    values = {answer[0] for answer in [*COST_EXPECTED.values(), *PLAN_EXPECTED.values()]}
    values.update(scenario_oracles())
    values.update(CURRENT_COST_EXPECTED.values())
    return values


@lru_cache(maxsize=1)
def scenario_oracles():
    # Scenario source records are deterministic; mutable fixture oracles above
    # remain read on every call so tests can replace them without stale results.
    from .benchmark_cases import scenarios
    from .semantic_cases import semantic_scenarios
    from .claims_cases import claims_scenarios
    return frozenset(v for s in [*scenarios().values(), *semantic_scenarios().values(), *claims_scenarios().values()] if s.expected
                     for v in s.expected.values() if type(v) is int)


def formats(value):
    dollars = Decimal(value) / 100
    return {str(value), f'{value:,}', f'{dollars:.2f}', f'${dollars:.2f}',
            *([f'${int(dollars)}'] if value % 100 == 0 else [])}


def numeric_source_spans(source):
    """Only numeric syntax is exempt, never numbers embedded in Python prose."""
    lines = source.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    return [(offsets[t.start[0]-1] + t.start[1], offsets[t.end[0]-1] + t.end[1])
            for t in tokenize.generate_tokens(io.StringIO(source).readline)
            if t.type == tokenize.NUMBER]


def leak_problems(text, *, sources=(), allowed_spans=(), values=None):
    """Exceptions are exact positions, so approving a threshold cannot hide a total."""
    allowed = list(allowed_spans)
    for source in sources:
        start = text.find(source)
        if start >= 0:
            allowed.extend((start+a, start+b) for a, b in numeric_source_spans(source))
    problems = []
    for value in oracle_values() if values is None else values:
        for token in formats(value):
            for match in re.finditer(r'(?<![\w.])' + re.escape(token) + r'(?![\w.])', text):
                if not any(a <= match.start() and match.end() <= b for a, b in allowed):
                    problems.append(f'oracle-like value at {match.start()}: {token}')
    return sorted(set(problems))


def policy_spans(text):
    """Recognized constraint/budget syntax, not arbitrary prose containing numbers."""
    patterns = [r'Performance risk must be at most (1\.0)',
                r'At least (14) days of (?:cpu|memory) evidence',
                r'(?:cpu|memory) p95 must be at most (60\.0|80\.0)%',
                r'Input should be (?:less than or equal to|greater than or equal to) (0|4|100)(?![\d.])',
                r'SQL row limit exceeded \((100)\)',
                r'^- max_\w+: (\d+(?:\.\d+)?)$',
                r'^(?:Field|Scope) `[^`]+`: .*$',
                r'^Trusted input `[^`]+`: .*$',
                r'^Constraint: .*$',
                r'^review_by: \d{4}-\d{2}-\d{2}$',
                r'\[rule [^\]]+\]']
    spans = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.M):
            if pattern.startswith(('^(?:Field', '^Trusted', '^Constraint')):
                if '<!-- generated:start -->' not in text or '<!-- generated:end -->' not in text:
                    continue
                if not text.index('<!-- generated:start -->') < match.start() < text.index('<!-- generated:end -->'):
                    continue
            spans.append(match.span(1) if match.lastindex else match.span())
    return spans

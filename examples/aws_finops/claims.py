"""Handwritten descriptive claims; query scope and question requirements are host-owned."""

from pydantic import Field, model_validator

from agent_pathcheck import current_evidence
from agent_pathcheck.extensions.claims import Claim, RequiredClaim, settle


QUESTION_TEXT = {
    'comparison': 'Explain the total change in production NetAmortizedCost from September to October 2026 '
                  'and the change for every service appearing in either month, treating absence as zero. '
                  'Order services by change descending, then service name ascending.',
    'top-services': 'Report the current total production NetAmortizedCost for October 2026 and up to three services '
                    'with the largest signed changes since September. Rank changes descending, then service name '
                    'ascending, treating a missing service as zero.',
}


def required_claims(models, question, account, months):
    if question not in QUESTION_TEXT:
        raise ValueError('Unknown cost-claims question')
    requests = [models['CostQuery'](account=account, month=month, metric='NetAmortizedCost',
                                   group_by='SERVICE').model_dump(mode='json') for month in months]
    before, after = requests
    if question == 'top-services':
        return (RequiredClaim(models['ServiceChanges'], {'before': before, 'after': after}, {'top': 3}),
                RequiredClaim(models['CostTotal'], {'source': after}))
    return (RequiredClaim(models['CostDelta'], {'before': {'source': before}, 'after': {'source': after}}),
            RequiredClaim(models['ServiceChanges'], {'before': before, 'after': after}, {'top': None}))


def claim_input(data):
    return data.model_dump() if isinstance(data, Claim) else data


def claim_types(CostQuery, ServiceChange):
    class CostTotal(Claim):
        source: CostQuery
        value: int | None = None

        @model_validator(mode='before')
        @classmethod
        def check(cls, data):
            data = claim_input(data)
            if not isinstance(data, dict) or 'source' not in data:
                return data
            source = CostQuery.model_validate(data['source'])
            actual = sum(row.cost_cents for row in current_evidence().require(source))
            return settle(data, actual, 'claims.cost_total')

    class CostDelta(Claim):
        before: CostTotal
        after: CostTotal
        value: int | None = None

        @model_validator(mode='before')
        @classmethod
        def check(cls, data):
            data = claim_input(data)
            if not isinstance(data, dict) or not {'before', 'after'} <= data.keys():
                return data
            before = CostTotal.model_validate(data['before'])
            after = CostTotal.model_validate(data['after'])
            return settle(data, after.value - before.value, 'claims.cost_delta')

    class ServiceChanges(Claim):
        before: CostQuery
        after: CostQuery
        top: int | None = Field(default=None, gt=0)
        rows: list[ServiceChange] | None = None

        @model_validator(mode='before')
        @classmethod
        def check(cls, data):
            data = claim_input(data)
            if not isinstance(data, dict) or not {'before', 'after'} <= data.keys():
                return data
            top = data.get('top')
            if top is not None and (type(top) is not int or top <= 0):
                return data  # Strict field validation reports invalid selection sizes.
            before = CostQuery.model_validate(data['before'])
            after = CostQuery.model_validate(data['after'])
            old = {row.service: row.cost_cents for row in current_evidence().require(before)}
            new = {row.service: row.cost_cents for row in current_evidence().require(after)}
            changes = [(service, new.get(service, 0) - old.get(service, 0)) for service in old.keys() | new.keys()]
            changes.sort(key=lambda item: (-item[1], item[0]))
            actual = [dict(service=service, change_cents=value) for service, value in changes[:top]]
            if isinstance(data.get('rows'), list):
                data = {**data, 'rows': [row.model_dump() if isinstance(row, ServiceChange) else row
                                         for row in data['rows']]}
            return settle(data, actual, 'claims.service_changes', field='rows')

    return CostTotal, CostDelta, ServiceChanges


def money(cents):
    magnitude = abs(cents)
    return f'{"-" if cents < 0 else ""}${magnitude // 100:,}.{magnitude % 100:02d}'


def formatters(models):
    return {models['CostTotal']: money, models['CostDelta']: money,
            models['ServiceChanges']: lambda rows: '; '.join(
                f"{row['service']}: {money(row['change_cents'])}" for row in rows) or 'no services'}


def projection(result, question='comparison'):
    """Fixture grading selects required content, ignoring keys, prose, and extras."""
    delta = changes = total = None
    for claim in result['claims'].values():
        if ('value' in claim and 'before' in claim
                and claim['before'].get('source', {}).get('month') == '2026-09'
                and claim['after'].get('source', {}).get('month') == '2026-10'):
            delta = claim['value']
        if ('rows' in claim and claim['before']['month'] == '2026-09'
                and claim['after']['month'] == '2026-10'
                and claim['top'] == (3 if question == 'top-services' else None)):
            changes = claim['rows']
        if 'source' in claim and claim['source']['month'] == '2026-10':
            total = claim['value']
    if question == 'top-services':
        return dict(current_total_cents=total, services=changes)
    return dict(total_change_cents=delta, services=changes)

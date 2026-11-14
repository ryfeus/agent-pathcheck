"""Host-only claims witnesses, adversarial programs and independent oracles."""

from dataclasses import dataclass

from .benchmark_cases import Scenario, cost_scenario, scenarios


@dataclass(frozen=True)
class ClaimScenario(Scenario):
    question: str = 'comparison'

    def host_options(self):
        return {**super().host_options(), 'question': self.question}


def claims_scenarios():
    cases = {'claims_' + name: ClaimScenario('claims_' + name, 'cost-claims', old.policy, old.sql,
            old.expected, old.challenge, old.blocked_by) for name, old in scenarios().items() if old.case == 'costs'}
    pairs = {'Compute': (100, 170), 'Database': (50, 110), 'Storage': (20, 50),
             'Retired': (20, None), 'Credits': (0, -10)}
    base = cost_scenario('claims_top_services', pairs, 'A different question using the same output types')
    expected = dict(current_total_cents=sum(after for _, after in pairs.values() if after is not None),
                    services=base.expected['services'][:3])
    cases[base.name] = ClaimScenario(base.name, 'cost-claims', base.policy, base.sql, expected,
                                    base.challenge, question='top-services')
    return cases


def claim_witness(question='comparison'):
    return {'comparison': CLAIMS_WITNESS, 'top-services': TOP_SERVICES_WITNESS}[question]


CLAIMS_READS = '''\
before = CostQuery(account=account, month=previous_period, metric="NetAmortizedCost", group_by="SERVICE")
after = CostQuery(account=account, month=period, metric="NetAmortizedCost", group_by="SERVICE")
before_rows = query(before)
after_rows = query(after)
'''
CLAIMS_WITNESS = CLAIMS_READS + '''\
delta = CostDelta(before=CostTotal(source=before), after=CostTotal(source=after))
services = ServiceChanges(before=before, after=after, top=None)
Answer(claims={"delta": delta, "services": services}, text="Total change: {delta}. Changes by service: {services}.")
'''
TOP_SERVICES_WITNESS = CLAIMS_READS + '''\
current = CostTotal(source=after)
services = ServiceChanges(before=before, after=after, top=3)
Answer(claims={"current": current, "services": services}, text="Current total: {current}. Largest changes: {services}.")
'''
SUPPLIED_CHANGES = '''\
changes = {}
for row in before_rows:
    changes[row.service] = -row.cost_cents
for row in after_rows:
    changes[row.service] = changes.get(row.service, 0) + row.cost_cents
ordered = sorted(changes, key=lambda service: (-changes[service], service))
'''


def negative_probes():
    supplied = 'rows=[{"service": s, "change_cents": changes[s]} for s in ordered]'
    return [
        ('claims_wrong_total', 'cost_basic', CLAIMS_WITNESS.replace('CostTotal(source=after)',
            'CostTotal(source=after, value=sum(r.cost_cents for r in after_rows)+1)'), 'claims.cost_total'),
        ('claims_before_read', 'cost_basic', 'CostTotal(source=CostQuery(account=account, month=period, '
            'metric="NetAmortizedCost", group_by="SERVICE"))', 'evidence.required'),
        ('claims_dropped_service', 'cost_basic', (CLAIMS_READS + SUPPLIED_CHANGES
            + CLAIMS_WITNESS[len(CLAIMS_READS):]).replace('top=None)', supplied.replace('in ordered]', 'in ordered[:-1]]') + ')'),
            'claims.service_changes'),
        ('claims_reverse_ties', 'cost_ties_unicode', (CLAIMS_READS + SUPPLIED_CHANGES
            + CLAIMS_WITNESS[len(CLAIMS_READS):]).replace('top=None)', supplied.replace('in ordered]', 'in list(reversed(ordered))]') + ')'),
            'claims.service_changes'),
        ('claims_literal_number', 'cost_basic', CLAIMS_WITNESS.replace('Total change:', 'Total change 7:'), 'claims.text_numbers'),
        ('claims_missing_placeholder', 'cost_basic', CLAIMS_WITNESS.replace('{delta}', '{absent}'), 'claims.text_placeholder'),
        ('claims_guessing', 'cost_basic', CLAIMS_READS + '''\
actual = sum(row.cost_cents for row in after_rows)
for guess in [actual - 1, actual]:
    try:
        CostTotal(source=after, value=guess)
    except ValueError:
        pass
''' + CLAIMS_WITNESS[len(CLAIMS_READS):], 'claims.cost_total'),
        ('claims_raw_dict', 'cost_basic', '{"claims": {}, "text": "Pretend answer"}', 'validated Answer'),
        ('claims_top_wrong_period', 'top_services', TOP_SERVICES_WITNESS.replace(
            'CostTotal(source=after)', 'CostTotal(source=before)'), 'claims.required_missing'),
    ]

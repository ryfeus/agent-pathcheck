"""Opt-in semantic demonstrations, separate from the historical benchmark."""

from dataclasses import replace
from .benchmark_cases import scenarios, COST_WITNESS


def caveat_scenarios():
    cases = scenarios()
    result = {}
    for name, source, caveats in [('cost_caveats_churn', 'cost_churn_refunds', ['costs.service_churn']),
                                  ('cost_caveats_stable', 'cost_basic', [])]:
        old = cases[source]
        result[name] = replace(old, name=name, policy='costs-with-caveats',
                               expected={**old.expected, 'caveats': caveats})
    return result


def cost_witness():
    return COST_WITNESS.replace('CostComparison(',
        'churn = {r.service for r in before} != {r.service for r in after}\n'
        'CostComparison(caveats=["costs.service_churn"] if churn else [], ')


def minimum_scenarios():
    from .benchmark_cases import fleet, plan_scenario
    result = {}
    for name, count in [('min_savings_basic',12), ('min_savings_boundary',3)]:
        data=fleet(count)
        if count == 3:
            data['recommendations'] = [(r,i,t,risk,7999+n) for n,(r,i,t,risk,_) in enumerate(data['recommendations'])]
        base=plan_scenario(name,data,'Minimum savings boundary')
        savings={row[0]:row[-1] for row in data['recommendations']}
        selected=[p for p in base.expected['proposals'] if savings[p['recommendation_id']] >= 8000]
        expected=dict(proposals=selected,monthly_savings_cents=sum(savings[p['recommendation_id']] for p in selected),
                      outcome='recommendations' if selected else 'no_eligible_recommendations')
        result[name]=replace(base,policy='memory-required-min-savings',expected=expected)
    # This historical seeded scenario has one eligible recommendation below the floor.
    old=scenarios()['plan_seed_41']
    result['min_savings_seed_41']=replace(old,name='min_savings_seed_41',policy='memory-required-min-savings',
        expected=dict(proposals=[],monthly_savings_cents=0,outcome='no_eligible_recommendations'))
    return result


def semantic_scenarios():
    return {**caveat_scenarios(), **minimum_scenarios()}


def semantic_witness(scenario):
    from .benchmark_cases import witness
    if scenario.policy == 'costs-with-caveats':
        return cost_witness()
    code=witness(scenario)
    if scenario.policy == 'memory-required-min-savings':
        code=code.replace('c.monthly_savings_cents <= 0', 'c.monthly_savings_cents < minimum_savings_cents')
    return code

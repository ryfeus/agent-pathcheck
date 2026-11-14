"""Offline acceptance demonstration for existing and corrected FinOps policies."""

import json
from agent_pathcheck.acceptance import policy_acceptance
from agent_pathcheck.semantic import check_pages
from .benchmark_cases import scenarios, witness, negative_probes
from .semantic import ROOT, hygiene
from .semantic_config import declarations
from .policy import operations_for
from .semantic_cases import minimum_scenarios, semantic_witness
from .tracer import domain_for, replays_for, LIMITS

PROBE_RULES = {
    'cost_wrong_total':'costs.total','cost_missing_service':'costs.service_changes','cost_reverse_ties':'costs.service_changes',
    'cost_float_cents':'pydantic.int_type','cost_stale_constant':'costs.total',
    'plan_missing_memory_query':'rightsizing.metric_required','plan_duplicate_instances':'rightsizing.unique_instance',
    'plan_wrong_order':'rightsizing.top_candidates','plan_wrong_total':'rightsizing.total',
    'plan_claim_empty':'rightsizing.top_candidates','plan_wrong_empty_outcome':'rightsizing.outcome',
    'plan_float_savings':'pydantic.int_type','wrong_account':'pydantic.literal_error','wrong_period':'pydantic.literal_error',
    'query_budget':'limit.max_queries','infinite_loop':'limit.max_feed_duration_secs',
}


def semantic_check(policy):
    case='costs' if 'CostQuery' in policy.parameters['models'] else 'rightsizing'
    links,words=declarations(policy)
    ops=operations_for(policy.parameters["models"],None)
    return check_pages(ROOT/case/policy.name,policy,links,words,ops,hygiene=hygiene)


def factory(scenario):
    return domain_for(case=scenario.case,policy=scenario.policy,fixture_sql=scenario.sql)


def acceptance_cases(corrected=False):
    if corrected:
        cases=minimum_scenarios()
        bad=witness(cases['min_savings_boundary'])
        probes=[dict(name='corrected_minimum',scenario='min_savings_boundary',code=bad,rule_id='rightsizing.min_savings')]
        workflows={name:semantic_witness(s) for name,s in cases.items()}
    else:
        cases={name:s for name,s in scenarios().items() if not s.blocked_by}
        probes=[]
        classes={'raw_sql':'runtime','host_evidence_access':'runtime','fabricated_success_dict':'contract'}
        for name,scenario,code,_ in negative_probes():
            if scenario in cases:
                probes.append(dict(name=name,scenario=scenario,code=code,
                                   **({'rule_id':PROBE_RULES[name]} if name in PROBE_RULES else {'failure_class':classes[name]})))
        workflows={name:witness(s) for name,s in cases.items()}
    return cases,workflows,probes


def acceptance(corrected=False):
    cases,workflows,probes=acceptance_cases(corrected)
    return policy_acceptance(factory,cases,workflows,probes,{n:replays_for(s.case) for n,s in cases.items()},
                             semantic_check=semantic_check,limits=LIMITS)


def main():
    results={name:acceptance(corrected) for name,corrected in [('existing',False),('min-savings',True)]}
    print(json.dumps({name:{k:v for k,v in result.items() if k!='checks'} for name,result in results.items()},indent=2))
    if not all(r['passed'] for r in results.values()):
        print(json.dumps({name:[{'name':c['name'],'kind':c['kind'],'problems':c.get('problems'),
                                'errors':c.get('report',{}).get('errors')} for c in r['checks'] if not c['passed']]
                          for name,r in results.items()},indent=2))
        raise SystemExit(1)


if __name__=='__main__':
    main()

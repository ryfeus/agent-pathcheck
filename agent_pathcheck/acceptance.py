"""Offline policy acceptance over explicit domains, workflows, and probes."""

from .extensions.engine import Engine
from .presentation import validated_fields
from .runtime import run
from .safety import unsupported_constructs


def policy_acceptance(policy_factory, scenarios, reference_workflows, negative_probes, replays,
                      *, semantic_check, limits=None):
    checks, reports, verified = [], [], {}
    with Engine() as engine:
        for name, scenario in scenarios.items():
            domain=policy_factory(scenario)
            identity = (domain.policy.policy_hash, domain.context.get('docs_hash'))
            if identity not in verified:
                try:
                    safety=unsupported_constructs(domain.policy.contract)
                except Exception as exc:
                    safety = [str(exc)]
                try:
                    semantic=semantic_check(domain.policy)
                except Exception as exc:
                    semantic = [str(exc)]
                verified[identity] = safety, semantic
            safety, semantic = verified[identity]
            checks.append(dict(name=name,kind='contract_safety',passed=not safety,problems=safety))
            checks.append(dict(name=name,kind='semantic_pages',passed=not semantic,problems=semantic))
            trials=replays.get(name, ())
            report=run(reference_workflows[name],domain=domain,engine=engine,limits=limits,
                       replays=trials,require_replays=bool(trials)).report
            passed=validated_fields(report)['validation_status']=='passed'
            checks.append(dict(name=name,kind='reference',passed=passed,report=report))
            reports.append(report)
        for probe in negative_probes:
            domain=policy_factory(scenarios[probe['scenario']])
            report=run(probe['code'],domain=domain,engine=engine,limits=limits).report
            failure=report.get('failure') or {}
            expected=(failure.get('rule_id') == probe['rule_id'] if probe.get('rule_id')
                      else failure.get('class') == probe['failure_class'])
            passed=validated_fields(report)['validation_status']!='passed' and expected
            checks.append(dict(name=probe['name'],kind='negative_probe',passed=passed,report=report))
            reports.append(report)
    inconclusive=sum(t.get('verdict')=='inconclusive' for r in reports for t in r.get('replays',[]))
    return dict(passed=all(c['passed'] for c in checks),checks=checks,replay_inconclusive=inconclusive,
                checks_passed=sum(c['passed'] for c in checks),checks_total=len(checks))

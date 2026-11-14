"""Compare retained submissions under an explicit correction, without a model."""

import argparse
from collections import Counter
import json
from pathlib import Path

from agent_pathcheck import run
from agent_pathcheck.extensions.engine import Engine
from agent_pathcheck.presentation import validated_fields
from .benchmark import source_identity
from .benchmark_cases import scenarios
from .replay import verify_artifact
from .tracer import domain_for, LIMITS

CORRECTIONS = Path(__file__).with_name('corrections')


def load_correction(name):
    if name != 'min-savings':
        raise ValueError('Unknown correction; available: min-savings')
    return json.loads((CORRECTIONS / (name + '.json')).read_text())


def correction_impact(paths, *, correction='min-savings', manifest_path):
    change=load_correction(correction)
    manifest=json.loads(Path(manifest_path).read_text())
    contents=[(Path(path),Path(path).read_bytes()) for path in paths]
    hashes={str(path):verify_artifact(path,manifest,content=data) for path,data in contents}
    cases=scenarios(); rows=[]
    with Engine() as engine:
        for path,data in contents:
            for trial in json.loads(data)['trials']:
                scenario=cases[trial['scenario']]
                for number,call in enumerate(trial['agent'].get('tool_calls',[]),1):
                    if 'code' not in call.get('arguments',{}):
                        continue
                    item=dict(source=str(path),scenario=scenario.name,submission=number)
                    if scenario.case != change['question_case'] or scenario.policy != change['base_mode']:
                        rows.append({**item,'impact':'outside_correction'})
                        continue
                    reports=[]
                    for mode in (change['base_mode'],change['candidate_mode']):
                        domain=domain_for(case=scenario.case,policy=mode,fixture_sql=scenario.sql,max_claim_attempts=None)
                        report=run(call['arguments']['code'],domain=domain,engine=engine,limits=LIMITS).report
                        reports.append({**report,**validated_fields(report)})
                    before,after=reports
                    a,b=before['validated_result'],after['validated_result']
                    differences={key:dict(before=a.get(key),after=b.get(key))
                                 for key in ('proposals','monthly_savings_cents','outcome')
                                 if a is not None and b is not None and a.get(key)!=b.get(key)}
                    impact=('invalidated' if a is not None and b is None else
                            'newly_accepted' if a is None and b is not None else
                            'changed' if differences else 'unchanged' if a is not None else 'still_rejected')
                    rows.append({**item,'impact':impact,'differences':differences,'base':before,'candidate':after})
    return dict(correction=correction,settings={'max_claim_attempts':None},input_sha256=hashes,
                **source_identity(),summary=dict(Counter(row['impact'] for row in rows)),submissions=rows)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--correction',required=True,choices=['min-savings'])
    parser.add_argument('--artifacts',required=True,nargs='+',type=Path)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    if args.output.exists():
        parser.error('Output already exists; choose a new dated file')
    result=correction_impact(args.artifacts,correction=args.correction,manifest_path=args.manifest)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result,stream,indent=2,allow_nan=False);stream.write('\n')
    print(json.dumps(result['summary']))


if __name__=='__main__':
    main()

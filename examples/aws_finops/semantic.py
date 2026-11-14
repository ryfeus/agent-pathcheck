"""Generate or check the FinOps semantic pages, entirely offline."""

import argparse
from pathlib import Path
import re

from agent_pathcheck.semantic import build_pages, check_pages, check_declarations, policy_models, write_pages
from .hygiene import leak_problems, policy_spans
from .policy import build_policy, operations_for, modes_for
from .queries import metadata
from .semantic_config import declarations

ROOT = Path(__file__).with_name('semantic')


def variants():
    return [(case, mode) for case in ('costs', 'rightsizing') for mode in modes_for(case)]


def claim_variants():
    from .claims import QUESTION_TEXT
    return [('cost-claims', mode, question) for mode in modes_for('cost-claims') for question in QUESTION_TEXT]


def directory_for(case, mode, question=None):
    return ROOT / case / (question or 'comparison') / mode if case == 'cost-claims' else ROOT / case / mode


def configuration(case, mode, *, question=None):
    policy = build_policy(case, mode, question=question)
    operations = operations_for(policy.parameters['models'], None)
    relationships, vocabulary = declarations(policy)
    problems = check_declarations(relationships, vocabulary, policy_models(policy, operations).values(), metadata.tables)
    if problems:
        raise ValueError('; '.join(problems))
    return policy, relationships, vocabulary, operations


def hygiene(text):
    sources = re.findall(r'```python\n(.*?)\n```', text.split('<!-- generated:end -->', 1)[0], re.S)
    return leak_problems(text, sources=sources, allowed_spans=policy_spans(text))


def check(case, mode, directory=None, *, question=None):
    root = directory_for(case, mode, question) if directory is None else directory
    problems = check_pages(root, *configuration(case, mode, question=question), hygiene=hygiene)
    index = Path(root) / 'index.md'
    if index.exists() and len(index.read_text()) > 3000:
        problems.append('index.md: exceeds 3000 character budget')
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--write', action='store_true')
    action.add_argument('--check', action='store_true')
    args = parser.parse_args()
    problems = []
    configurations = [(case, mode, None) for case, mode in variants()] + claim_variants()
    for case, mode, question in configurations:
        if args.write:
            write_pages(directory_for(case, mode, question), build_pages(*configuration(case, mode, question=question)))
        else:
            problems.extend(f'{case}/{mode}/{p}' for p in check(case, mode, question=question))
    for problem in problems:
        print(problem)
    print(f'Semantic {"generation" if args.write else "check"}: {len(configurations)} trees, {len(problems)} problems')
    if problems:
        raise SystemExit(1)


if __name__ == '__main__':
    main()

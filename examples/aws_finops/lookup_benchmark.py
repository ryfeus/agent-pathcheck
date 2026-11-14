"""Explicit semantic retrieval tasks, graded separately from execution evidence.

This tests instructed retrieval and accurate reporting, not spontaneous lookup
demand, semantic understanding, or the correctness of arbitrary model prose.
"""

from dataclasses import asdict, dataclass
from hashlib import sha256
import json

from mcp import Client

from agent_pathcheck.semantic import SemanticReader, frontmatter
from .benchmark_cases import Scenario, scenarios
from .mcp_server import create_server
from .policy import build_policy
from .semantic import ROOT
from .semantic_cases import semantic_scenarios


@dataclass(frozen=True)
class LookupScenario(Scenario):
    required_rules: tuple[str, ...] = ()


def lookup_scenarios():
    sources = {**scenarios(), **semantic_scenarios()}
    definitions = [
        ('lookup_missing_memory', 'plan_missing_memory',
         ('rightsizing.instance_evidence', 'rightsizing.coverage')),
        ('lookup_service_churn', 'cost_caveats_churn',
         ('costs.service_churn', 'costs.caveats_required')),
        ('lookup_min_savings', 'min_savings_boundary',
         ('rightsizing.min_savings', 'rightsizing.top_candidates')),
    ]
    return {name: LookupScenario(**{**asdict(sources[source]), 'name': name,
                                   'challenge': 'Retrieve rule provenance and rationale before computing the answer'},
                                 required_rules=rules)
            for name, source, rules in definitions}


def lookup_instructions(scenario):
    if not isinstance(scenario, LookupScenario):
        return ''
    pages = '\n'.join(f'- rules/{rule_id}' for rule_id in scenario.required_rules)
    return (
        'Additional task: this is an explicit semantic-documentation retrieval benchmark. '
        'Before your first run_workflow submission, call read_semantic for each of these rule pages:\n'
        f'{pages}\n'
        'Read their responses before composing your first workflow; do not batch these reads with run_workflow. '
        'Use their provenance and rationale, then solve the configured workflow normally, including required replay. '
        'Documentation guides planning and must not be passed as data to guest constructors or the evidence ledger. '
        'Your final response must be a JSON object without Markdown fences with exactly these keys: '
        '"validated_result" (the actual checked workflow result) and "semantic_notes" (one object per requested rule). '
        'Each note must contain exactly "rule_id", "level", "source", and "why". '
        'Copy level from the page frontmatter, source from its generated Source field, and why from the generated '
        'rationale paragraph after Source and before References. Preserve the full wording, not a paraphrase; '
        'do not substitute the authored Rationale section. These notes are a separate retrieval answer, '
        'not part of the validated workflow result. A correct workflow alone does not complete this task.'
    )


def lookup_expectations(scenario):
    """Host-only expectations. Never insert these answers into model instructions."""
    policy = build_policy(scenario.case, scenario.policy)
    reader = SemanticReader(ROOT / scenario.case / scenario.policy)
    notes, pages = [], []
    for rule_id in scenario.required_rules:
        rule = policy.rules[rule_id]
        path = f'rules/{rule_id}.md'
        text = reader.read(path)
        metadata = frontmatter(text)
        if (metadata['level'] != rule.level.value or f'Source: {rule.source}' not in text
                or rule.why not in text.split('<!-- generated:end -->', 1)[0]):
            raise ValueError(f'{path}: rule metadata and semantic page disagree')
        notes.append(dict(rule_id=rule_id, level=rule.level.value, source=rule.source, why=rule.why))
        pages.append(dict(path=path, rule_id=rule_id, content_sha256=sha256(text.encode()).hexdigest()))
    return dict(docs_hash=reader.docs_hash, semantic_notes=notes, pages=pages)


def grade_lookup(scenario, result):
    """Require successful relevant reads before execution, plus exact metadata.

    This returns benchmark diagnostics only. It never modifies an execution
    report, validated_answer, or the evidence ledger.
    """
    expected = lookup_expectations(scenario)
    activity = result.get('tool_activity', [])
    first_workflow = next((e for e in activity if e['name'] == 'run_workflow'), None)
    reads = result.get('lookups', [])
    retrieved = set()
    for event in activity:
        index = event.get('entry_index')
        if (event['name'] != 'read_semantic' or first_workflow is None
                or event['seq'] >= first_workflow['seq'] or event['turn'] >= first_workflow['turn']
                or type(index) is not int or not 0 <= index < len(reads) or not reads[index].get('ok')):
            continue
        retrieved.add(event.get('content_sha256'))
    missing = [page['path'] for page in expected['pages'] if page['content_sha256'] not in retrieved]
    problems = []
    if missing:
        problems.append('Required pages were not successfully read before the first workflow submission')
    context = (result.get('last_report') or {}).get('context', {})
    if context.get('docs_hash') != expected['docs_hash']:
        problems.append('Workflow documentation snapshot does not match the graded pages')
    notes_ok, result_echo_ok = False, False
    try:
        answer = json.loads(result.get('final_text') or '')
        if not isinstance(answer, dict) or set(answer) != {'validated_result', 'semantic_notes'}:
            raise ValueError('Expected a JSON object with validated_result and semantic_notes')
        notes = answer['semantic_notes']
        if not isinstance(notes, list) or len(notes) != len(expected['semantic_notes']):
            raise ValueError('Expected one semantic note per required rule')
        normalized = {}
        for note in notes:
            if (not isinstance(note, dict) or set(note) != {'rule_id', 'level', 'source', 'why'}
                    or not all(isinstance(value, str) for value in note.values())
                    or note['rule_id'] in normalized):
                raise ValueError('Semantic notes need unique rule IDs and exactly the requested string fields')
            normalized[note['rule_id']] = {**note, 'why': ' '.join(note['why'].split())}
        wanted = {note['rule_id']: {**note, 'why': ' '.join(note['why'].split())}
                  for note in expected['semantic_notes']}
        notes_ok = normalized == wanted
        result_echo_ok = (result.get('validated_result') is not None
                          and json.dumps(answer['validated_result'], sort_keys=True)
                          == json.dumps(result['validated_result'], sort_keys=True))
    except (ValueError, TypeError) as exc:
        problems.append(str(exc))
    if not notes_ok:
        problems.append('Semantic notes do not match the requested rule metadata and rationale')
    if not result_echo_ok:
        problems.append('Final JSON does not reproduce the checked workflow result')
    return dict(passed=not problems, required_pages=[p['path'] for p in expected['pages']],
                missing_before_workflow=missing, notes_match=notes_ok, result_echo_match=result_echo_ok,
                docs_hash=expected['docs_hash'], expected_notes=expected['semantic_notes'],
                page_sha256={p['path']: p['content_sha256'] for p in expected['pages']}, problems=problems)


async def lookup_preflight(cases):
    """Exercise the real MCP text response without a model or a workflow submission."""
    checks = []
    for scenario in cases.values():
        expected = lookup_expectations(scenario)
        async with Client(create_server(**scenario.host_options())) as client:
            for page in expected['pages']:
                reply = await client.call_tool('read_semantic', {'name': page['path']})
                text = '\n'.join(part.text for part in reply.content if hasattr(part, 'text'))
                digest = sha256(text.encode()).hexdigest()
                checks.append(dict(scenario=scenario.name, page=page['path'], content_sha256=digest,
                                   matched=not reply.is_error and digest == page['content_sha256']))
    return dict(checks=len(checks), matched=sum(c['matched'] for c in checks), rows=checks)

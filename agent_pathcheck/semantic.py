"""Code-backed semantic declarations and plain Markdown documentation."""

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
from urllib.parse import unquote, urlsplit
import warnings

from .rendering import model_source, type_source

from typing import Literal, get_args, get_origin

from pydantic import BaseModel


@dataclass(frozen=True)
class Relationship:
    name: str
    source: type[BaseModel]
    source_field: str
    target: type[BaseModel] | str
    target_field: str
    description: str


@dataclass(frozen=True)
class Vocabulary:
    term: str
    model: type[BaseModel]
    field: str
    value: str


def check_declarations(relationships, vocabulary, models, catalogs):
    problems = []
    for link in relationships:
        if link.source not in models or link.source_field not in link.source.model_fields:
            problems.append(f'{link.name}: unknown source field')
        if isinstance(link.target, str):
            if link.target not in catalogs or link.target_field not in catalogs[link.target].c:
                problems.append(f'{link.name}: unknown catalog field')
        elif link.target not in models or link.target_field not in link.target.model_fields:
            problems.append(f'{link.name}: unknown target field')
    for entry in vocabulary:
        field = entry.model.model_fields.get(entry.field)
        if (entry.model not in models or field is None or get_origin(field.annotation) is not Literal
                or entry.value not in get_args(field.annotation)):
            problems.append(f'{entry.term}: value is not in the active Literal scope')
    return problems


# The generated prefix includes YAML metadata, the title, and the marked body.
# Everything after END is authored and preserved byte-for-byte.
START = '<!-- generated:start -->'
END = '<!-- generated:end -->'
AUTHORED = '\n\n## Meaning\n\n\n## Notes (advice, not checked)\n\n\n## Rationale\n\n'
LINK = re.compile(r'\[[^\]]*\]\(([^\s)]+)\)')


def snake(name):
    return re.sub(r'(?<!^)(?=[A-Z])', '_', name).lower()


def policy_models(policy, operations):
    found = {}
    def visit(model):
        if not isinstance(model, type) or not issubclass(model, BaseModel) or model.__name__ in found:
            return
        found[model.__name__] = model
        def nested(annotation):
            if get_origin(annotation):
                for item in get_args(annotation):
                    nested(item)
            else:
                visit(annotation)
        for field in model.model_fields.values():
            nested(field.annotation)
    for model in [*policy.parameters.get('models', {}).values(), policy.output_model,
                  *[m for op in operations for m in (op.request_type, op.result_type)]]:
        visit(model)
    return found


def page_parts(text):
    if text.count(START) != 1 or text.count(END) != 1 or text.index(START) > text.index(END):
        raise ValueError('expected exactly one ordered pair of generated markers')
    at = text.index(END) + len(END)
    return text[:at], text[at:]


def frontmatter(text):
    if not text.startswith('---\n') or '\n---\n' not in text[4:]:
        raise ValueError('missing frontmatter')
    result = {}
    for line in text.split('\n---\n', 1)[0].splitlines()[1:]:
        key, separator, value = line.partition(':')
        if not separator or key in result:
            raise ValueError('invalid or duplicate frontmatter key')
        value = value.strip()
        result[key] = json.loads(value) if value.startswith('"') else value
    return result


def build_pages(policy, relationships, vocabulary, operations):
    models = policy_models(policy, operations)
    requests = {op.request_type for op in operations}
    paths = {model: f'{"measures" if model in requests else "entities"}/{snake(name)}.md'
             for name, model in models.items()}
    pages = {}
    module = policy.build.__module__
    def link(origin, target, label):
        return f'[{label}]({posixpath.relpath(target, posixpath.dirname(origin) or ".")})'
    def add(path, anchor, kind, title, body, **metadata):
        header = {'anchor': anchor, 'kind': kind, **metadata}
        prefix = '---\n' + ''.join(f'{k}: {json.dumps(v)}\n' for k, v in header.items())
        pages[path] = prefix + f'---\n# {title}\n\n{START}\n' + body.strip() + '\n' + END + AUTHORED
    for model, path in paths.items():
        source = model_source(model)
        fields = [f'Field `{name}`: `{type_source(field.annotation)}`; '
                  f'{"required" if field.is_required() else "optional"}; constraints `{field.metadata!r}`.'
                  for name, field in model.model_fields.items()]
        returned = [link(path, paths[op.request_type], op.request_type.__name__) for op in operations if op.result_type is model]
        referenced = [key for key in policy.rules if repr(key) in source or f'"{key}"' in source]
        related = [link(path, f'relationships/{r.name}.md', r.name) for r in relationships
                   if r.source is model or r.target is model]
        body = '\n'.join(fields) + '\n\n```python\n' + source + '\n```\n'
        if returned:
            body += '\nReturned by: ' + ', '.join(returned) + '\n'
        for op in operations:
            if op.request_type is model:
                body += '\nReturns ' + link(path, paths[op.result_type], op.result_type.__name__) + (
                    ' as a validated batch.' if op.many else ' as exactly one validated row.') + '\n'
        if referenced:
            body += '\nRules: ' + ', '.join(link(path, f'rules/{key}.md', key) for key in referenced) + '\n'
        if related:
            body += '\nRelationships: ' + ', '.join(related)
        add(path, f'{module}:{model.__name__}', 'measure' if model in requests else 'entity', model.__name__, body)
    for relationship in relationships:
        path = f'relationships/{relationship.name}.md'
        target = (f'catalog `{relationship.target}.{relationship.target_field}`' if isinstance(relationship.target, str)
                  else link(path, paths[relationship.target], f'{relationship.target.__name__}.{relationship.target_field}'))
        body = link(path, paths[relationship.source], f'{relationship.source.__name__}.{relationship.source_field}')
        add(path, f'{module}:relationship:{relationship.name}', 'relationship', relationship.name,
            body + ' → ' + target + '\n\n' + relationship.description)
    for key, rule in sorted(policy.rules.items()):
        path = f'rules/{key}.md'
        refs = [link(path, target, model.__name__) for model, target in paths.items()
                if key in model_source(model)]
        maturity = 'advice, not checked' if rule.level.value == 'documented' else (
            'guidance, not enforced' if rule.level.value == 'guided' else rule.level.value)
        body = f'{rule.summary}\n\nLevel: {maturity}\n\nOwner: {rule.owner}\n\nSource: {rule.source}\n\n{rule.why}'
        body += '\n\nReferences: ' + (', '.join(refs) if refs else link(path, 'index.md', 'Active policy and operations'))
        add(path, f'rule:{key}', 'rule', key, body, level=rule.level.value, owner=rule.owner,
            **({'review_by': rule.review_by} if rule.review_by else {}))
    add('rules/index.md', f'{module}:{policy.name}:rules', 'index', 'Rules', '\n'.join(
        '- ' + link('rules/index.md', f'rules/{key}.md', key) for key in sorted(policy.rules)))
    add('glossary.md', f'{module}:{policy.name}:glossary', 'glossary', 'Glossary', '\n'.join(
        f'- {v.term}: `{v.value}` in ' + link('glossary.md', paths[v.model], f'{v.model.__name__}.{v.field}')
        for v in vocabulary))
    # The compact map links to a complete rule directory instead of truncating it.
    index = ['Policy: ' + policy.name, 'Documentation guides planning; it is never evidence.']
    if policy.parameters.get('required_claims'):
        index += ['\nThis question requires: ' + '; '.join(
            spec.description() for spec in policy.parameters['required_claims'])]
    for group, label in [('entities/', 'Entities'), ('measures/', 'Measures'), ('relationships/', 'Relationships')]:
        index += ['\n' + label + ': ' + ', '.join(link('index.md', p, PurePosixPath(p).stem)
                                                   for p in sorted(pages) if p.startswith(group))]
    index += ['\n[Rules](rules/index.md) · [Vocabulary](glossary.md)']
    add('index.md', f'{module}:{policy.name}:index', 'index', 'Semantic map', '\n'.join(index))
    return pages


def write_pages(directory, pages):
    directory = Path(directory)
    for name, fresh in pages.items():
        path = directory / name
        if not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError("Semantic output escapes its root")
        prefix, authored = page_parts(fresh)
        if path.exists():
            _, authored = page_parts(path.read_text())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(prefix + authored)


def check_pages(directory, policy, relationships, vocabulary, operations, *, hygiene=None, today=None):
    directory = Path(directory)
    expected = build_pages(policy, relationships, vocabulary, operations)
    problems, seen = [], set()
    actual = {}
    for path in directory.rglob('*.md'):
        name = path.relative_to(directory).as_posix()
        if not path.resolve().is_relative_to(directory.resolve()):
            problems.append(f'{name}: page escapes semantic root')
        else:
            actual[name] = path.read_text()
    for name in sorted(expected.keys() - actual.keys()):
        problems.append(f'{name}: anchor has no page')
    for name, text in actual.items():
        try:
            meta = frontmatter(text)
            anchor = meta.get('anchor')
            if not anchor:
                problems.append(f'{name}: missing anchor')
            if anchor in seen:
                problems.append(f'{name}: duplicate anchor')
            seen.add(anchor)
            if name not in expected or anchor != frontmatter(expected[name])['anchor']:
                problems.append(f'{name}: unknown anchor or page')
            prefix, authored = page_parts(text)
            if name in expected and prefix != page_parts(expected[name])[0]:
                problems.append(f'{name}: generated content or metadata differs')
            headings = re.findall(r'^#+ (.+)$', authored, re.M)
            allowed = ['Meaning', 'Notes (advice, not checked)', 'Rationale']
            if headings != allowed:
                problems.append(f'{name}: unexpected authored headings')
            rationale = authored.split('## Rationale\n', 1)[-1] if '## Rationale\n' in authored else ''
            rule_links = [posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
                          for target in LINK.findall(rationale)]
            if not any(p.startswith('rules/') and p in expected for p in rule_links):
                problems.append(f'{name}: Rationale requires a rule link')
            if meta.get('review_by') and date.fromisoformat(meta['review_by']) < (today or date.today()):
                warnings.warn(f'{name}: rule review is overdue', stacklevel=2)
            for target in LINK.findall(re.sub(r'```.*?```', '', text, flags=re.S)):
                parsed = urlsplit(target)
                if parsed.scheme or parsed.netloc:
                    continue
                destination = (directory / name).parent / unquote(parsed.path)
                if not destination.resolve().is_relative_to(directory.resolve()) or not destination.is_file():
                    problems.append(f'{name}: broken relative link {target}')
                elif parsed.fragment:
                    headings = re.findall(r'^#+ (.+)$', destination.read_text(), re.M)
                    slugs = [re.sub(r'[^\w -]', '', h.lower()).replace(' ', '-') for h in headings]
                    if unquote(parsed.fragment) not in slugs:
                        problems.append(f'{name}: broken relative anchor {target}')
            if hygiene:
                problems.extend(f'{name}: {problem}' for problem in hygiene(text))
        except (ValueError, TypeError, KeyError) as exc:
            problems.append(f'{name}: {exc}')
    return problems


def docs_hash(pages):
    return sha256(json.dumps(sorted(pages.items()), ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


class SemanticReader:
    """Immutable startup snapshot. Lookup is independent of execution/evidence."""

    def __init__(self, directory):
        from types import MappingProxyType
        root = Path(directory).resolve()
        pages = {}
        aliases = {}
        for path in sorted(root.rglob('*.md')):
            if not path.resolve().is_relative_to(root):
                raise ValueError('Semantic page escapes its root')
            name = path.relative_to(root).as_posix()
            text = path.read_text()
            pages[name] = text
            anchor = frontmatter(text)['anchor']
            for alias in (anchor, anchor.rsplit(':', 1)[-1]):
                aliases.setdefault(alias, set()).add(name)
        if 'index.md' not in pages:
            raise ValueError('Semantic root must contain index.md')
        self.pages = MappingProxyType(pages)
        self.aliases = {key: next(iter(names)) for key, names in aliases.items() if len(names) == 1}
        self.docs_hash = docs_hash(pages)

    def read(self, name):
        from difflib import get_close_matches
        if (not isinstance(name, str) or not name or len(name) > 256 or '\\' in name or '\x00' in name
                or name.startswith('/') or '..' in name.split('/')):
            raise ValueError('Use a page name inside the active semantic tree')
        key = self.aliases.get(name, name if name.endswith('.md') else name + '.md')
        if key not in self.pages:
            matches = get_close_matches(name, [*self.aliases, *self.pages], n=3)
            raise ValueError('Unknown semantic page. Close matches: ' + ', '.join(matches))
        return self.pages[key]

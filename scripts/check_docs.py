"""Offline Markdown links, claims test references, and retained-results budget."""

import ast
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r'\[([^\]]*)\]\(([^\s)]+)(?:\s+"[^"]*")?\)')


def prose(text):
    return re.sub(r'^\s*(`{3,}|~{3,})[^\n]*\n.*?^\s*\1\s*$', '', text, flags=re.M | re.S)


def anchors(text):
    found, counts = set(), {}
    for heading in re.findall(r'^#{1,6}\s+(.+?)\s*#*$', prose(text), re.M):
        heading = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', heading)
        slug = re.sub(r'[^\w\- ]', '', heading.replace('`', '').lower()).replace(' ', '-')
        index = counts.get(slug, 0)
        counts[slug] = index + 1
        found.add(f'{slug}-{index}' if index else slug)
    found.update(re.findall(r'(?:id|name)=["\']([^"\']+)["\']', text))
    return found


def test_names(path):
    return {f'{node.name}.{method.name}' for node in ast.parse(path.read_text()).body
            if isinstance(node, ast.ClassDef) for method in node.body
            if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name.startswith('test_')}


def check():
    files = set(subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard'],
                                       cwd=ROOT, text=True).splitlines())
    names = [name for name in files if name.endswith('.md')]
    errors, links = [], 0
    for name in sorted(set(names)):
        document = ROOT / name
        if not document.is_file():
            continue
        for label, destination in LINK.findall(prose(document.read_text())):
            parsed = urlsplit(destination.strip('<>'))
            if parsed.scheme or parsed.netloc:
                continue
            path = (document.parent / unquote(parsed.path)).resolve() if parsed.path else document
            links += 1
            if not path.exists():
                errors.append(f'{name}: missing {destination}')
            elif parsed.fragment and path.suffix == '.md' and unquote(parsed.fragment) not in anchors(path.read_text()):
                errors.append(f'{name}: missing anchor {destination}')
            if name == 'docs/claims.md' and path.suffix == '.py' and path.is_file():
                symbol = label.strip('`')
                if symbol not in test_names(path):
                    errors.append(f'{name}: missing test {symbol} in {destination}')
    size = sum((ROOT / name).stat().st_size for name in files if name.startswith('examples/')
               and '/results/' in name and (ROOT / name).is_file())
    if size >= 1_000_000:
        errors.append(f'Retained results use {size:,} bytes; budget is below 1,000,000.')
    for error in errors:
        print(error, file=sys.stderr)
    print(f'Checked {links} local links and claims; retained results: {size:,} bytes.')
    return bool(errors)


if __name__ == '__main__':
    raise SystemExit(check())

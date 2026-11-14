"""Export each diagram's inline SVG without external rendering dependencies."""

from pathlib import Path
import re
from xml.etree import ElementTree


directory = Path(__file__).resolve().parent
for source in sorted(directory.glob("*.html")):
    match = re.search(r"<svg\b.*?</svg>", source.read_text(), flags=re.DOTALL)
    if match is None:
        raise SystemExit(f"No SVG found in {source}")
    svg = match.group()
    ElementTree.fromstring(svg)
    output = source.with_suffix(".svg")
    output.write_text('<?xml version="1.0" encoding="UTF-8"?>\n' + svg + "\n")
    print(output)

# Policy diagrams

[reference-architecture.html](reference-architecture.html) is the editable source.
[reference-architecture.svg](reference-architecture.svg) is the diagram embedded
in the repository READMEs. Both are self-contained and use the Rustem classic
theme: `rustem-v1` colors and `space-grotesk-plex` typography. The unchanged font
subsets are embedded for offline rendering; their licenses are retained in
[font-licenses.txt](font-licenses.txt).

The overview shows the governed typed-query path. It groups the optional model
controller and MCP transport, and combines domain contracts, private evidence and
validators into one ownership block. The raw-SQL comparison, retry wiring, and
individual sales/FinOps operations are covered in the example documentation.
Arrows show calls or data flow, not a requirement that every verdict passes:
FinOps presentation uses its domain verdict independently of fixture grading.

The [dynamic-policy one-pager](../policy-as-types-one-pager.md) embeds two
additional diagrams in the same classic style:

- [Enforcement timing](dynamic-policy-timing.html)
  ([SVG](dynamic-policy-timing.svg)): compares controls before planning, during
  the workflow, and at final acceptance. These are integration patterns; Cedar
  and Pydantic can also enforce checks earlier.
- [CPU/memory evidence](dynamic-policy-evidence.html)
  ([SVG](dynamic-policy-evidence.svg)): shows the agent's requests, host-validated
  observations, private evidence, proposal rejection, and final plan checks.
  The proposal gate uses both CPU and memory with at least 14 days of coverage
  each; final acceptance rechecks eligibility, selection, and savings. These
  checks are conceptual domain controls, independent of fixture grading.

After editing the HTML, regenerate the SVG from the repository root:

```bash
python3 docs/diagrams/export_svg.py
```

The exporter handles all three HTML sources, uses only the standard library,
and verifies each SVG is valid XML. The HTML is static, with an accessible SVG
title and description and no network dependencies. Inspect it at full size and
at a typical document width after changing the layout.

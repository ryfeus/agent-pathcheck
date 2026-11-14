# Level 1: SQL behind typed requests

This adds scoped SQLite reads and policy-dependent workflows to
[Level 0](../minimal/README.md). Pydantic request types constrain which period and
aggregation the agent can ask for. The [claims table](../../docs/claims.md) links
scope, read-only access, batch validation, and presentation guarantees to tests.

Sales deliberately uses an **independent fixture answer** to check final arithmetic.
It has no semantic final-output contract, so a checked answer has basis
`fixture_answer`. [Level 2: FinOps](../aws_finops/README.md) adds evidence-backed
domain output validation independently of its fixture grader.

## Run

Use Python 3.12 from the repository root:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r examples/sql_math_tracer/requirements.txt
python -m unittest discover -s examples/sql_math_tracer -v
```

Tests and the MCP server need no API key. To run the optional model loop, set
`OPENAI_API_KEY` in the environment or repository-root `.env`:

```bash
python -m examples.sql_math_tracer.agent --model MODEL_ID --case margin --policy totals
python -m examples.sql_math_tracer.agent --model MODEL_ID --case margin --policy rows
python -m examples.sql_math_tracer.agent --model MODEL_ID --case change --fixture variant --policy rows
```

Host options are `--case revenue|margin|change`, `--fixture base|variant`, and
`--policy totals|rows`. Defaults are `margin`, `base`, and `totals`. Tool callers
cannot change these options. `--env-file` selects a different host credential file.

## The policy is Python

[policy.py](policy.py) builds `SalesQuery` with literal fields from trusted task
context. The October margin task permits `period="2026-10"`; the comparison task
permits both October and September. Under the totals policy, `aggregation="sum"`
returns one `SalesTotals` object. Under the rows policy, `aggregation="rows"`
returns a list of validated `Sale` objects.

The renderer advertises the same types used by operation dispatch. Request
construction runs Pydantic on the host; only the exact registered request type can
reach the fixed, parameterized SQL. A dictionary, raw SQL string, or invented class
is not a typed query. This is runtime enforcement, not static approval of a program.

Under the totals policy, a complete submitted workflow is:

```python
totals = query(SalesQuery(period=period, aggregation="sum"))
(totals.revenue_cents - totals.cogs_cents) / totals.revenue_cents
```

Under the rows policy:

```python
rows = query(SalesQuery(period=period, aggregation="rows"))
revenue = sum(row.revenue_cents for row in rows)
cogs = sum(row.cogs_cents for row in rows)
(revenue - cogs) / revenue
```

Both produce `0.324` on the base fixture and `0.25` on the variant. Submitting the
totals workflow under the rows policy fails before SQL. Revenue uses integer cents;
margin is a ratio; margin change is a percentage-point difference. The final Python
expression is the answer. Constructors and guest values are supplied, so submitted
code should not import or redefine them.

## MCP and the agent loop

The server exposes one tool, `run_workflow(code: str)`:

```bash
python -m examples.sql_math_tracer.mcp_server --case margin --policy totals
```

Each call creates fresh guest state, a database, and private evidence. Expected
answers and fixture rows are not advertised. The shared controller uses the explicit `--model` selection, low reasoning effort, 2,500 output tokens per turn, at most eight
turns and six tool calls. It can inspect failures and repair within that budget.
Only the controller receives API credentials; the guest receives no shell,
filesystem, account connectors, or credential tools. Trusted host Python is not
an operating-system sandbox.

## Reports and limitations

`execution` records runtime completion. `contract_check="not_applicable"` reflects
the absence of a semantic output contract. `answer_check` records the independent
fixture grade. `validated_answer` is published only when execution and grading
pass; its basis is `fixture_answer`.

The latest attempt controls publication. Earlier successful events cannot override
a later failed or malformed call. Model `final_text` stays unverified, even when it
sounds confident. Reports retain the typed requests, constructor events, SQL,
bound parameters, and returned rows that actually ran.

A correct constant can pass after a successful query. This example does not prove
derivation, data authenticity, or unencoded business rules. Read the
[claims and limits](../../docs/claims.md) before copying the pattern.

## Baseline comparison

[tracer.py](tracer.py) retains the explicit `run_raw_sql_baseline` comparison.
It is separate from the typed execution path and does not provide the same scope
constraints.

The [core guide](../../agent_pathcheck/README.md), [adaptation guide](../../docs/adapt.md),
and [offline CI](../../.github/workflows/tests.yml) describe shared interfaces and checks.

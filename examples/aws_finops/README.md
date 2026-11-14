# Level 2: evidence-backed FinOps decisions

This adds cross-step domain validation, ranked selection, and exact arithmetic to
[Level 1: sales](../sql_math_tracer/README.md). FinOps recomputes its final answer
from host-observed evidence; its independent fixture grader remains a separate
check. The [claims table](../../docs/claims.md) names the tests behind each guarantee.

All data is synthetic. Recommendations are returned as data; no AWS access or
changes occur. The example includes optional counterfactual replay and typed
abstention. SQLAlchemy generates row/request fields with explicit refinements;
Pydantic validators still define every business rule.

## Run locally

Using Python 3.12 from the repository root:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r examples/sql_math_tracer/requirements.txt
python -m examples.aws_finops.mcp_server --demo --case costs
python -m examples.aws_finops.mcp_server --demo --case rightsizing --policy cpu-only
python -m examples.aws_finops.mcp_server --demo --case rightsizing --policy memory-required
python -m unittest discover -s examples/aws_finops -v
```

Local demos and tests need no API key. To use selected model, set `OPENAI_API_KEY` in the
host environment or repository-root `.env`, then replace `--demo` with
`--agent --model MODEL_ID`, selecting a model available to your API key.
Run without either flag to start the stdio MCP server. It exposes
`run_workflow(code: str)` and the read-only `read_semantic(name: str)`; the description supplies the actual contract, question,
inputs, and limits.

| Case | Output |
| --- | --- |
| `costs` | A complete cost comparison |
| `rightsizing` | An evidence-backed decision contract |
| `cost-claims` | Composed answer claims; select `comparison` or `top-services` with `--question` |

The [composable-claims guide](../../docs/composable-claims.md) explains required
sources, host-filled values and the checked text template. Try both questions
with the same claim vocabulary:

```bash
python -m examples.aws_finops.mcp_server --demo --case cost-claims --question comparison
python -m examples.aws_finops.mcp_server --demo --case cost-claims --question top-services
python -m examples.aws_finops.benchmark --claims-cases --replays
```

The default question is `comparison`. Both existing policy names `cpu-only` and
`memory-required` are supported for this cost-only case. It allows multiple valid
claims but permanently fails an invocation after its first rejected claim.

## Schema and query builders

[queries.py](queries.py) declares costs, instances, instance types, recommendations,
and observations. Foreign keys link recommendations and observations to instances,
and recommended targets to the catalog. The candidate query gets target architecture
from that catalog. The same metadata creates the SQLite schema with foreign keys
enabled, then [fixture.sql](fixture.sql) supplies seed rows.

Invalid fixture relationships block the first required operation with
`data.integrity`. The catalog is synthetic and does not model target capacity,
pricing, regional availability, or a live AWS catalog. The host compiles fixed
SQLAlchemy statements with bound parameters; the bounded SQLite adapter executes
them. Guests choose typed requests, not arbitrary SQL.

## Simple: compare comparable costs

The host exposes `CostQuery` with account `production`, months `2026-09` and
`2026-10`, metric `NetAmortizedCost`, and grouping `SERVICE`. Invalid scope fails
construction before SQL runs. Returned `ServiceCost` rows are strict models.

Both monthly queries must complete. `CostComparison` recomputes every service
difference, treating a missing service as zero. It requires descending signed change,
ascending service names for ties, and the exact integer-cent total. Missing,
duplicate, misordered, or fabricated deltas fail even if the grand total matches.

| Base fixture service | Monthly change |
| --- | ---: |
| EC2 | +$4,500 |
| EC2-Other | +$600 |
| RDS | +$500 |
| ECS (new) | +$400 |
| S3 | +$300 |
| Lambda | −$100 |
| CloudFront (absent in October) | −$300 |
| Total | +$5,900 |

This attributes arithmetic changes to services. It does not infer business causes.

## Medium: evidence must precede a proposal

`CandidatesQuery(account=account)` returns generated, strict `Candidate` rows.
`EvidenceQuery(account=account, metric=metric)` returns generated `Evidence` rows.
The active policy permits CPU alone or CPU plus memory. Both queries are scoped
to production; required queries must complete even when their result is empty.

`RightsizeProposal(recommendation_id=...)` checks discovery, compatible architecture,
performance risk at most 1.0, positive savings, and observations for the same instance.
Each required metric needs at least 14 days of coverage. CPU p95 must be at most
60%; memory p95 at most 80%. Missing required observations exclude a candidate.

`RightsizingPlan` recomputes the best five eligible recommendations, or all if fewer
exist, ordered by savings descending then recommendation ID ascending, with at most
one recommendation per instance. Its savings must equal the observed sum. No eligible
recommendations requires an empty list, zero savings, and `no_eligible_recommendations`.
The base fixture yields $500/month under CPU-only policy and $430/month when memory
is required. These are invented estimated savings, not a forecast.

## Optional replay and abstention

```python
from examples.aws_finops.tracer import replays_for, rightsizing_workflow, run_checked

report = run_checked(rightsizing_workflow(), replays=replays_for("rightsizing"),
                     require_replays=True)
```

Arithmetic replay changes scoped costs and positive savings. Ranking replay changes
the positive candidate order while preserving eligibility, and observes the ordered
proposal IDs. A fixed selection cannot pass merely by recalculating its total.

An unchanged signal is `inconclusive`. Host callbacks independently examine evidence
to justify empty results or invariant selections. These exemptions remain visible;
failed trials cannot be exempted. Required replay blocks publication unless each
configured trial passes or has an explicit exemption. Advisory replay records its
outcomes without gating the base answer. Replays make no model calls and do not
prove general data dependence.

`Abstain(kind, rule_id, detail)` needs a matching unresolved block on an exact request
returned by the policy's `required_requests` callback. A failed unrelated request,
optional operation, recovered query, or deliberately exhausted call budget cannot
authorize refusal. A checked abstention has `result.kind="abstained"` and differs
from a valid empty plan.

## Inspect and change the policy

- [policy.py](policy.py): generated fields, literal scope, required requests, and handwritten semantic validators.
- [queries.py](queries.py): table relationships and bounded read statements.
- [counterfactuals.py](counterfactuals.py): trusted perturbations and evidence-derived exemptions.
- [tracer.py](tracer.py): invocation setup, independent fixture answers, and reference workflows.
- [mcp_server.py](mcp_server.py): contract discovery and demo/agent entry points.

`execution` records runtime completion; `contract_check` records the domain verdict;
`answer_check` records separate fixture grading. `validation_status` also applies
required replay. `validated_answer` contains the host-checked context and result with
basis `domain_contract`. Display that payload as the checked answer. Model prose
remains unverified, and a later failed attempt clears prior publication.

Correct constants can match observed data. The system does not prove arbitrary
computational derivation, authenticate the source data, or supply rules nobody
encoded. The [claims table](../../docs/claims.md) makes these limits explicit.

## Verification

[The benchmark](BENCHMARK.md) runs deterministic synthetic datasets and deliberate
negative probes. It also documents optional model trials and replay of your own
saved submissions. No live-run results or external artifact downloads are required.

## Executable semantic layer

The [semantic-layer guide](../../docs/semantic-layer.md) explains generated pages,
rule maturity, claim limits, progressive lookup, and the correction-to-rule loop.
The `costs` and `rightsizing` cases allow one final claim construction per invocation; candidate
filters remain usable for eligibility checks. Saved-submission replay uses the explicit
unlimited setting. Documentation is planning context and never ledger evidence.

| Comes from code or schema | Policy decision, written explicitly | Hand-written logic |
| --- | --- | --- |
| Field names, types, nullability, relationships | Value ranges, `Literal` scope, units, rule levels | Cross-step validators, selection, arithmetic, caveat derivation |

```bash
python -m examples.aws_finops.semantic --check
python -m examples.aws_finops.mcp_server --demo --case costs --policy costs-with-caveats
python -m examples.aws_finops.acceptance
python -m examples.aws_finops.benchmark --semantic-cases --replays
```

The new modes are opt-in. `costs-with-caveats` carries a service-churn explanation;
`memory-required-min-savings` demonstrates a correction with an inclusive
8,000-cent minimum and is excluded from default demo prompts and benchmarks.

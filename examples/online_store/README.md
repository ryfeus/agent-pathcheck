# Online store: ten levels of executable policy

One synthetic store connects scoped lookups, arithmetic, relationships, rankings,
shared concepts, justified refusal, corrections, composite answers, and policy
composition. The [original domain description](domain.md) is retained as source
material. The executable choices below resolve details that description left open.

The examples use the existing `agent_pathcheck` runtime and a fresh SQLite fixture
for each invocation. No refund, inventory change, promotion change, or external
store access occurs. Money is integer USD cents. Forecasts are synthetic inputs.

## Run

From the repository root with Python 3.12:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r examples/online_store/requirements.txt
python -m examples.online_store --demo --case category-comparison
python -m examples.online_store --demo --case restock --replays
python -m examples.online_store --all --replays
```

These commands need no API key. `--all` runs reference scenarios, independent
fixture grading, semantic checks, negative probes, shared-window variants, and
the correction demonstration. `--replays` additionally requires the configured
arithmetic and ranking perturbations to pass.

## The ten-level walkthrough

The levels here follow the supplied store description, independently of the
minimal/Sales/FinOps levels in the repository introduction.

| Level | CLI case or option | Checked base result |
| --- | --- | --- |
| 1: scoped lookup | `--case revenue` | October production net revenue: **$362,000** |
| 2: evidence-backed count | `--case shipments` | **2** orders shipped in the completed October 19–25 week |
| 3: comparison | `--case category-comparison` | **$303,000 → $362,000**, change **+$59,000**, including Outdoor's **−$3,000** |
| 4: relationship-bound refund | `--case refund` | Order 1042's **$500** request is eligible for automatic approval |
| 5: eligibility and ranking | `--case restock` | **P-101, P-102, P-107, P-108, P-109**, forecast total **$13,100** |
| 6: shared concepts | `--case loyalty` and `--case churn` | Newly Gold: C-future, C-new, C-window; churn cohort: C-churn |
| 7: justified refusal | `--case product-margin` | Checked abstention for P-120's missing cost |
| 7: partial data | `--case return-rate --fixture truncated-returns` | Checked abstention for the exact cohort's row-limit block |
| 8: correction | `--case restock --policy minimum-monthly-sales` | P-110 replaces P-109; forecast total **$12,950** |
| 9: composite answer | `--case promotion-review` | West net revenue declines **$11,000**; pause **PROMO-app** |
| 10: composed policy | `--case composed` | Q3 Gold-customer late orders: **S-1: 2**, **S-2: 1** |

Use `--demo` with any row. Without the truncated fixture, `return-rate` returns
2 returned units / 543 units sold. The cohort denominator includes the same
production/status restrictions as financial reads.

The supplier ranking counts an order once for each supplier represented on it.
Its total of three is **supplier/order associations across two distinct late
orders**. Two lines supplied by the same supplier still count once. The report
labels this unit `order_supplier_pairs`. This establishes an association with a
delay, not its cause.

### Change one shared definition

```bash
python -m examples.online_store --demo --case loyalty --active-days 60
python -m examples.online_store --demo --case churn --active-days 60
```

The same configuration changes both policies: C-window drops out of newly Gold,
and C-window plus C-not-gold enter the churn cohort. The C-window fixture combines
a last order 76 days before cutoff with an old refund falling out of the annual
revenue window. The `affected_policies("active_customer")` interface also names
composed policies using the Gold filter. The definition and dependency map live in
[concepts.py](concepts.py), and all policy identities include their configuration.

### Turn a correction into a tested rule

[The retained correction](corrections/minimum-monthly-sales.json) records its owner,
rationale, stages and boundaries. The base policy documents the floor; the
corrected policy checks preceding-month product net revenue against an inclusive
5,000-cent minimum. Tests distinguish 4,900 cents from 5,000 cents.

```bash
python -m examples.online_store --demo --case restock --policy minimum-monthly-sales
python -m examples.online_store.acceptance --replays
```

Acceptance retains the old product selection, recomputes its total from current
evidence, and confirms it is rejected under the corrected policy. It then runs a
fresh selection workflow and verifies the changed result. Rule maturity is
reviewed code metadata; a runtime does not promote its own rules.

## Accounting and decision definitions

- The fixed default cutoff is **November 1, 2026** in **America/Los_Angeles**.
  Reporting intervals are half-open and converted to UTC on the host. Monthly
  cutoffs must be month starts; timezone transitions are handled by `zoneinfo`.
- Paid, shipped, delivered and refunded production orders qualify. Pending,
  cancelled and test orders do not contribute to measures. Sales and line-total
  discounts use order dates; refunds use return dates. Their aggregates are
  combined without multiplying sales by the number of returns.
- A return identifies a stable order line and its requesting customer. It must
  follow that order's delivery; cumulative refunds and quantities cannot exceed
  what was paid and purchased on that line. Refund requests are decisions,
  separate from completed returns. Days 30/31 and $500/$500.01 are explicit tests.
- Return rate is an **order-cohort rate**: returns against the selected month's
  lines observed before the cutoff divided by that cohort's purchased units.
  Returns of older orders still affect current-month revenue. An observed empty
  cohort has `rate: null`; incomplete data cannot produce a rate.
- Active customers have a qualifying order in the preceding configured number
  of local calendar days. Gold also needs at least $2,000 net revenue in the
  preceding twelve calendar months. Loyalty compares month-start/end snapshots.
  Churn uses inactivity `active_days <= age < active_days + 90`; its first day
  intentionally touches the active-window boundary. Gold filters in historical
  reports use each reporting period's end, never later customer activity.
- Restocking sums all known warehouses at the exact snapshot, counts distinct
  selling days for the same product over 90 days, and uses units sold over 30
  calendar days for velocity. Cover is compared by integer cross-multiplication.
  Monthly margin subtracts the current synthetic catalog cost of units sold from
  the preceding month's net revenue. This fixture does not model historical cost
  changes or reverse COGS when a refund occurs.
- Restock forecasts belong to a product and snapshot. Missing inventory, cost or
  forecast for a potentially eligible product blocks the required read. Known
  ineligibility can exclude a product without needing irrelevant missing facts.
  Forecast ranking does not certify forecast quality.
- Promotion discounts are integer basis points. Each assessment uses West-region
  category revenue and margin within the promotion/current-month overlap.
  Only declining categories require margin assessment. Percentage comparisons
  and ranking use exact rational arithmetic. Zero-sales promotions are excluded;
  unknown cost or nonpositive net revenue on a remaining candidate blocks a
  meaningful margin-rate comparison. The final pause claim independently verifies
  the declining-category join. Causal narrative remains unverified.
- Late delivery means more than five local calendar days after shipping.
  Undelivered orders already past that limit are included; future delivery
  observations do not change a historical snapshot.

Host callers can supply a strict `StoreConfig`, `RefundRequest`, or `PlanSpec`.
`--config-file` accepts serialized configuration; `--active-days` overrides its
activity window. These are host controls, outside workflow code.

## Policy composition and MCP

The example-local [planner](planner.py) accepts only these registered capabilities:

| Measure | Groupings | Shapes and filters |
| --- | --- | --- |
| Net revenue | Category, customer | Aggregate, comparison, top-k; region, channel, Gold |
| Shipped orders | Supplier, customer | Aggregate, comparison, top-k; region, channel, Gold |
| Late deliveries | Supplier, customer | Aggregate, comparison, top-k; region, channel, Gold |

A comparison needs two ordered, nonoverlapping periods; other shapes need one.
Top-k needs a positive count. Periods are calendar months or quarters ending no
later than the host cutoff. The compiler derives its relationship paths, scoped
requests, exact required reads, and ordinary `MetricResult` validator from the
registry. It has no supplier-question-specific branch. Unknown measures,
incompatible groups, SQL, validator overrides and inherited-rule overrides fail
before data access.

Start a fixed-policy stdio MCP server:

```bash
python -m examples.online_store.mcp_server --case restock
python -m examples.online_store.mcp_server --case composed \
  --plan-file examples/online_store/plans/gold-supplier-delays.json
```

The tools are `run_workflow(code: str)` and `read_semantic(name: str)`. The policy
and semantic snapshot are fixed for the server lifetime. Every workflow still
gets fresh guest state, SQLite data and private evidence.

For the optional natural-language planner, set `OPENAI_API_KEY` in the host
environment or repository-root `.env`:

```bash
python -m examples.online_store --agent --model MODEL_ID --case composed \
  --question "Which suppliers had the most late deliveries to Gold customers last quarter?"
```

Planning gets up to three attempts to submit a strict `PlanSpec`, without data
access. A successful plan is compiled and passed to the existing bounded workflow
loop. Failed planning never launches execution and cannot claim abstention.
Other cases also support `--agent` with the host-selected policy.

Production scope is inherited by every registered measure. It is not a channel
or a planner-supplied filter. The planner receives that scope and the host cutoff
explicitly; unsupported test-storefront questions still fail planning.

The workflow tool describes callable guest constructors separately from the host
validator source. A workflow ends with the output instance as its final
expression. Each invocation must collect its own evidence; a checked abstention
must catch the exact required read's `ValueError` and return an `Abstain` instance
in the same program. The host performs validation automatically.

The normalized plan and its plain-language summary accompany the checked answer.
Validation establishes correctness **under that plan**. Matching an unrestricted
natural-language question to the right plan is still an unverified interpretation.
The nineteen-question catalogue in the original description is a roadmap; only
the scenarios and registered combinations documented here are implemented.

### Real-model evaluation

The opt-in [live evaluator](live.py) runs all eleven cases, the corrected restock
policy, blocked returns, both 60-day customer variants, and three natural-language
questions. It requires `OPENAI_API_KEY` or the root `.env` and makes billable API
calls using your explicit model selection and the existing planning/workflow budgets:

```bash
python -m examples.online_store.live --model MODEL_ID \
  --output examples/online_store/results/local/my-live-run
python -m examples.online_store.live --model MODEL_ID --scenario planner-comparison \
  --output examples/online_store/results/local/my-planner-run
```

Use a new output directory for each run; existing trial artifacts are preserved.
The evaluator checks final validation, independent fixture results, and exact
normalized plans for the three planner questions. Expected answers and plans
remain outside model context. Revenue/comparison and restocking also require
their counterfactual replays. Full traces and a summary are written locally;
any failed scenario makes the command exit nonzero. Live runs are separate from
offline CI.

## Evidence, semantic pages and verification

The example-local APIs are `build_policy`, `compile_plan`, `domain_for`, and
`run_checked`. [policy.py](policy.py) defines validators, [queries.py](queries.py)
owns parameterized reads, and [workflows.py](workflows.py) contains guest examples.
`run_checked` keeps independent fixture grading separate from the domain verdict.
Presentation publishes `validated_answer` only after the required checks pass.

Decision policies permit one final claim construction per invocation. Restock
proposals are eligibility filters. Promotion answers use the existing composable
claims extension: required source identities bind the comparison and pause join,
and a rejected claim permanently prevents publication from that invocation.

Semantic pages are generated from the active policy and the authored explanations
in [semantic.py](semantic.py). They include all ten entities, relationships, rule
owners, provenance and maturity. The server freezes them in memory and includes
their hash in its report; fixture data and expected answers are never loaded by
the semantic generator. Generated pages can also be materialized for review:

```bash
python -m examples.online_store.semantic --check
python -m examples.online_store.semantic --write --output /tmp/store-semantics
python -m examples.online_store.semantic --check --output /tmp/store-semantics
python -m unittest discover -s examples/online_store -v
python -m examples.online_store.acceptance --replays
python scripts/check_docs.py
```

Materializing pages preserves authored tails using the shared semantic tooling.
The default `--check` checks the generated snapshots; supplying `--output` checks
the materialized trees, including stale generated content. Lookup does not commit
evidence. Tests cover exact scope, rejected batches, stale claims, partial reads,
relationship mismatches, correction boundaries, shared definitions, planner
tampering, counterfactuals, and MCP publication with mocked model responses.

As elsewhere in this repository, these checks do not authenticate upstream data,
prove arbitrary computational derivation, or verify causal explanations. See the
[tested guarantees](../../docs/claims.md) for the shared boundaries.

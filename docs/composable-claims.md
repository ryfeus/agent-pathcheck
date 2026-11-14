# Composable answer claims

The optional `cost-claims` case composes descriptive answers from small checked
claims. Here, **answer claims** means output objects; the [claims table](claims.md)
uses “claims” for the repository's tested guarantees.

## Two output tiers

Answer claims describe observed data. Each carries typed source requests and a
value that the host recomputes from completed evidence. An `Answer` combines them
with a text template. Different questions can reuse the same claim vocabulary.

Decision contracts such as `RightsizingPlan` still encode eligibility, selection,
ordering and the combined result together. Composable claims do not replace
those decision rules or change the existing `costs` and `rightsizing` cases.

| Answer claim | Sources | Checked result |
| --- | --- | --- |
| `CostTotal` | A complete scoped `CostQuery` | Sum of observed integer cents |
| `CostDelta` | Before and after `CostTotal` claims | After minus before |
| `ServiceChanges` | Before and after `CostQuery` requests | Ordered per-service differences |

Missing services count as zero. Changes sort by signed change descending, then
service name ascending. Negative changes remain eligible for this ranking.
`top=None` selects every service. A positive integer selects up to that many;
an oversized `top` returns all available services. Zero is not a valid `top`.

## Scope travels with the claim

Sources retain the account, month, metric and grouping. Building an out-of-scope
request fails before it reaches SQL. A claim requires the exact request to have
completed, even when it returned no rows. A nested claim is revalidated too.

The host renders each claim's type, full source scope, selection qualifiers and
checked value. Agent-authored labels and prose never determine the scope.
The checked answer retains the question, policy hash and documentation hash.

## Omitted and supplied values

Omit `value` or `rows`, or supply `None`, to ask the host to fill the result. This
is useful when the workflow only needs to compose evidence-backed descriptions.
Supply a value when checking arithmetic performed by the workflow is useful.
Both forms still require completed evidence and final output revalidation.

Filling happens in a Pydantic **before** validator. Returning a filled copy from
an after validator is ignored by the constructor path used by the pinned Monty
bridge. Before-validation filling preserves strict field checks and frozen
models. Nested instances and dictionaries receive the same evidence checks.

The following guest program uses constructors and inputs supplied by the host:

```python
before = CostQuery(account=account, month=previous_period,
                   metric="NetAmortizedCost", group_by="SERVICE")
after = CostQuery(account=account, month=period,
                  metric="NetAmortizedCost", group_by="SERVICE")
query(before)
query(after)
delta = CostDelta(before=CostTotal(source=before), after=CostTotal(source=after))
services = ServiceChanges(before=before, after=after)
Answer(claims={"delta": delta, "services": services},
       text="Total change: {delta}. Changes by service: {services}.")
```

## Requirements and presentation

The host declares `RequiredClaim` specifications using claim types, complete
source data and qualifiers. Matching ignores computed values and dictionary
keys. Extra claims are allowed. The same specifications generate the contract
requirement line and semantic index, and participate in the policy hash.

The comparison question requires the period delta and all service changes. The
top-services question requires a ranked prefix and the current total. Both use
the same output schema and validators. A question without requirements accepts
any nonempty set; its host-rendered scope is the only guard against presenting
an observation from the wrong part of the permitted scope.

Text accepts only simple `{name}` placeholders naming claims. Attribute access,
indexing, conversions, formatting syntax and literal digits outside placeholders
are rejected. Unicode digits are also rejected. Write dates as words or leave
them in host-rendered scope. The host formats cents as exact dollars, including
negative and very large values, without floating-point conversion.

`validated_result` retains the raw checked envelope. `validated_answer` also
contains rendered `claims` and substituted `text`. Required replay can withhold
publication even when the base contract passes. An unchanged result needs an
explicit exemption supported by host recomputation on both evidence ledgers.

## Extension wiring and limits

Import explicitly from `agent_pathcheck.extensions.claims`. Register request
types in `Policy.parameters['request_types']` from the domain's operations, and
declare the claim constructors and Answer in `Invocation.claims`. Use
`answer_model(types, required=specs)` to bind the domain vocabulary. Call
`render_answer` after validation while the policy context remains bound.

The new case sets `max_claim_rejections=0` and leaves the total-attempt cap unset.
A rejected claim or Answer permanently fails that invocation, even if caught;
repair requires a new workflow submission. Legacy domains retain their existing
budgets, and the new rejection limit defaults to `None`.

These checks cannot establish that an arbitrary claim answers the user's
question. Without required claims they cannot prevent cherry-picking. They do
not verify narrative meaning or causal explanations, including numbers written
as words. Each new aggregation still needs a handwritten claim type; this is
not a query compiler. Correct supplied constants may pass the base evidence;
replay detects particular stale constants, not general computational derivation.

Run the [FinOps demos](../examples/aws_finops/README.md) and the separate
[claims benchmark](../examples/aws_finops/BENCHMARK.md#composable-answer-claims)
offline. Generic factories and a share claim remain follow-ups.

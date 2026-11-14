# Executable semantic layer

The reference separates three roles: **define** the allowed requests and claims,
**check** them against host observations, and **guide** the workflow with explanations.
Its operating principle is **publish the rules, protect the facts, limit the claims,
audit the gap**. It remains a small Python reference, with no policy DSL, graph
store, persistent agent session, or model-based checker.

## Code and authored explanations

FinOps stores one Markdown tree per case and policy mode under
[`examples/aws_finops/semantic/`](../examples/aws_finops/semantic/).
The index leads to entities, measures, relationships, rules, and vocabulary.
Request fields ground scope; result rows do not acquire invented account or period
fields merely because their operation used those dimensions.

Generate and check the trees offline:

```bash
python -m examples.aws_finops.semantic --write
python -m examples.aws_finops.semantic --check
```

Generation updates code-owned frontmatter, the title, and the marked generated
body. It preserves everything after `<!-- generated:end -->` exactly. New pages
start with empty Meaning, Notes, and Rationale sections; an author supplies those
sections and a relative link to an applicable rule before the checker passes.
Notes are explicitly advice, not checked policy. Unknown anchors, missing pages,
stale generated content, unexpected authored headings, missing rationale links,
broken relative links, and oracle leakage fail checking. Overdue review dates warn.

Generated fields and validators describe policy, not fixture observations. The
hygiene checks recognize numeric code syntax and specific constraint messages by
position; an allowed threshold does not exempt that number elsewhere in prose.
They are regression checks against known oracle formats, not a proof against every
possible encoding of an answer.

## Rule maturity and identity

| Level | Meaning |
| --- | --- |
| `documented` | Advice, not checked |
| `guided` | Guidance that does not itself enforce eligibility |
| `checked` | Enforced by a validator or a typed constraint |
| `tested` | Enforced, with a demonstrated negative case and fixture |

`Rule` records identity, summary, owner, source, rationale, and an optional review
date. Unknown organizational ownership is `reference-example`. Rule IDs and levels
participate in `policy_hash`; explanatory metadata does not. `docs_hash` covers the
sorted paths and complete contents of the active page snapshot, including prose.
Changing an explanation changes the documentation identity without changing policy.

Guest validation hints include rule IDs. The active index adds at most 3,000
characters to the existing contract description. `read_semantic(name: str)` resolves
relative page names or anchors within the active tree. Lookups have their own
budget of eight attempts; workflows retain six submissions and eight model turns.
Malformed or unsuccessful lookups consume lookup attempts but preserve the last
workflow report. Only a workflow execution can establish a checked answer.

**Docs are never evidence.** The lookup tool runs outside Monty and cannot commit
anything to the evidence ledger. The server freezes its page snapshot at startup
and includes its hash in reports and checked-answer context.

## Claims, filters, and caveats

`Invocation.claims` identifies final claim constructors. By default, the policy
output type and enabled `Abstain` are claims. Aliases share their underlying type's
role and counters. Eligibility proposals remain filters: rejecting a candidate is
an ordinary part of selection. Host-side nested checks and final revalidation are
not additional guest attempts.

The core defaults to unlimited claims. New FinOps invocations allow one claim
construction in total, successful or rejected. A second construction makes the
invocation unpublishable even if caught. `Domain.max_constructions` optionally caps
all guest constructions per model. Neither cap can justify abstention. A fresh
workflow or replay gets fresh counters. Saved-submission replay explicitly disables the
claim cap so its behavior can be compared with earlier reports.

`Operation.annotate` can attach typed `Caveat` values to a validated batch. Rows and
annotations commit together; failed annotation leaves no partial evidence. Ledger
reads return copies. Output validators may also derive required caveats from
multiple completed batches.

The opt-in `costs-with-caveats` mode compares the two service sets and requires the
service-churn caveat when they differ. The checked output carries exact sorted rule
IDs, and checked-answer context carries the registry-owned explanation. The old
cost output and both existing rightsizing modes keep their business rules.

## Correction to tested policy

[`min-savings.json`](../examples/aws_finops/corrections/min-savings.json) retains the
human correction, previously acceptable workflow, proposed rule, and fixtures.
The new `memory-required-min-savings` mode applies an inclusive 8,000-cent monthly
minimum. This illustrative floor changes marginal selections while leaving the
basic control unchanged. It is an explicit trusted input, not a fixture answer.

```bash
python -m examples.aws_finops.acceptance
python -m examples.aws_finops.benchmark --semantic-cases --replays
python -m examples.aws_finops.correction_impact --correction min-savings \
  --artifacts tests/fixtures/semantic_correction_sample.json \
  --manifest tests/fixtures/semantic_correction_manifest.json \
  --output /tmp/semantic-impact-new.json
```

The acceptance harness checks reference workflows, expected negative failures,
contract safety, semantic pages, and counterfactuals without an LLM. Inconclusive
replays remain distinct; only explicit host exemptions permit required inconclusive
trials. A test deliberately weakens utilization enforcement and demonstrates that
a negative probe becomes accepted, causing acceptance to fail.

Impact analysis verifies artifact hashes before executing any retained code. It
holds claim limits equal across base and candidate policies so the correction is
the only changed behavior. It distinguishes changed checked results, invalidated
answers, continued rejection, and submissions outside the correction's scope.
It never presents an unchecked candidate result as a changed answer.

The [benchmark audit metrics](../examples/aws_finops/BENCHMARK.md#semantic-layer-audit-metrics)
expose rejected claims, abstention, inconclusive replay, lookup attempts, and
violation kinds. The correction mode is held out of default demo prompts and
benchmark selection. The opt-in lookup suite additionally asks the model to read
specific rule pages before submitting a workflow and reproduce their provenance
and rationale. It grades retrieval separately from execution, using page hashes
and tool-call order; documentation still cannot certify an answer.

```bash
python -m examples.aws_finops.benchmark --lookup-cases --replays
python -m examples.aws_finops.benchmark --lookup-cases --live --model MODEL_ID --replays \
  --output examples/aws_finops/results/local/my-lookup-run.json
```

The [lookup investigation](../examples/aws_finops/BENCHMARK.md#semantic-lookup-benchmark)
distinguishes instructed retrieval from spontaneous use. Broader live evaluation
and human review of authored explanations remain owner follow-ups.

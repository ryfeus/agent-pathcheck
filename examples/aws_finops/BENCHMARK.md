# FinOps benchmark

This harness checks workflows against deterministic, synthetic AWS-style fixtures.
It separates host contract validation, independent fixture grading, counterfactual
replay, and optional model performance. It does not use an AWS account or live
pricing. Results describe the selected scenarios, not general reliability.

## Offline checks

From the repository root, install the pinned requirements and run:

```bash
python -m pip install -r examples/sql_math_tracer/requirements.txt
python -m examples.aws_finops.benchmark
python -m examples.aws_finops.benchmark --replays
python -m examples.aws_finops.acceptance
```

The base suite covers cost comparisons, rightsizing selections, blocked inputs,
and deliberately invalid programs. Reference workflows must match their fixture
oracles; negative probes must fail for the expected reason. `--replays` also runs
trusted data perturbations in fresh invocations. A failed offline check exits
nonzero and prevents any requested live trials from starting.

Use `--only SCENARIO_NAME` to select cases. `benchmark_cases.py`, `semantic_cases.py`,
`lookup_benchmark.py`, and `claims_cases.py` define the named scenario sets and
expected results. Their oracles remain outside model context.

## Optional live runs

Set `OPENAI_API_KEY` in the host environment or repository-root `.env`. Select a
model available to that key using `--model`; there is no default model. The example
uses the Responses API with tool calls, low reasoning effort, and 2,500 output
tokens per turn. Its workflow budget is eight turns, six submissions, and eight
semantic lookups. Use a model that supports those settings.

```bash
python -m examples.aws_finops.benchmark --live --model MODEL_ID --replays \
  --output examples/aws_finops/results/local/my-benchmark.json
python -m examples.aws_finops.benchmark --live --model MODEL_ID --repeats 3 \
  --output examples/aws_finops/results/local/my-repeat-run.json
```

Live calls are opt-in and billable. `--live` requires a new output path; existing
files are never overwritten. Two isolated trials can run concurrently. The local
artifact records the selected model, source identity, settings, submissions,
reports, grades, and available usage. Credential-bearing exception messages are
not retained. Results directories are ignored by Git; no historical results or
private release assets are distributed here.

The summary distinguishes solved tasks, first-attempt success, repairs,
incorrect acceptance, infrastructure errors, expected blocks, and justified
abstention. Model prose cannot override a failed host verdict. Live model failures
are recorded as evaluation outcomes; a completed evaluation does not imply that
every trial passed. Inspect the summary, not just the command's exit status.

## Semantic-layer audit metrics

```bash
python -m examples.aws_finops.benchmark --semantic-cases --replays
python -m examples.aws_finops.semantic --check
```

The opt-in semantic suite covers caveats and the held-out minimum-savings
correction. Audit metrics include rejected claims, abstentions, inconclusive
replays, documentation lookups, and violation kinds. Base contract and replay
verdicts stay separate. An unchanged replay is inconclusive; only a trusted,
evidence-based exemption can permit publication when that replay is required.

See the [semantic-layer guide](../../docs/semantic-layer.md) for correction impact
analysis using bundled synthetic fixtures and explicit checksums.

## Semantic lookup benchmark

```bash
python -m examples.aws_finops.benchmark --lookup-cases --replays
python -m examples.aws_finops.benchmark --lookup-cases --live --model MODEL_ID --replays \
  --output examples/aws_finops/results/local/my-lookup-run.json
```

Lookup scenarios explicitly ask for relevant rule pages, provenance, and rationale.
The harness grades retrieval separately from execution using page hashes and
call order. Preflight runs offline. Successful instructed retrieval is not evidence
of spontaneous lookup, and documentation never commits execution evidence.

## Composable answer claims

```bash
python -m examples.aws_finops.benchmark --claims-cases
python -m examples.aws_finops.benchmark --claims-cases --replays
python -m examples.aws_finops.benchmark --claims-cases --live --model MODEL_ID --replays \
  --output examples/aws_finops/results/local/my-claims-run.json
```

This separate suite checks period comparisons and top-service answers assembled
from scoped claims. It tests exact sources, ordering, arithmetic, required claims,
rejected-claim budgets, checked templates, and counterfactual behavior. The
[claims guide](../../docs/composable-claims.md) describes the extension's boundaries.

## Replay saved submissions

Replay accepts user-supplied artifacts and a trusted SHA-256 manifest. Create the
manifest from known original bytes and preserve it separately from downloaded or
modified artifacts. Its minimal shape is:

```json
{"assets": {"saved.json": {"sha256": "SHA256_OF_ORIGINAL_FILE_BYTES"}}}
```

```bash
python -m examples.aws_finops.replay saved.json \
  --manifest trusted-manifest.json --output replay-new.json
```

Every input is verified before parsing submissions or starting the engine. A
matching checksum checks retained bytes; it does not authenticate data or establish
who produced them. The replay utility handles base-suite benchmark artifacts,
reexecutes their code with the corresponding scenarios, and compares the listed
verdict/result fields. It explicitly disables the final-claim attempt cap for
compatibility with earlier submissions. It does not silently apply the current
live submission budget or support every specialized suite.

## Claims and limits

The [claims table](../../docs/claims.md) links validation guarantees to tests.
Correct constants can pass a base contract after required reads. Particular
counterfactuals detect stale values and selections; they do not prove general
computational derivation. Upstream data authenticity, unencoded business rules,
and narrative explanations remain outside these checks.

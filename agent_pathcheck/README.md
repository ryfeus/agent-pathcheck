# Execution pattern and optional extensions

`agent_pathcheck` runs model-written Python in Monty against host-owned Pydantic
contracts and read-only operations. Each invocation has a new guest session and
private evidence ledger. An `Engine` can reuse the worker pool without sharing
variables, evidence, or databases.

Use Python 3.12 and the pinned example requirements. The runtime depends on
Pydantic and Monty; SQLite, SQLAlchemy type generation, MCP, and model-provider
adapters are separate modules. Domain code supplies business rules and query
builders. Fixture graders remain independent of domain validation.

## Pattern vs. extensions

| Pattern (package exports) | Optional, explicitly imported modules |
| --- | --- |
| `PolicyModel`, `Operation`, validation | `extensions.sqlalchemy`: schema field generation |
| `Evidence`, current invocation context | `extensions.abstain`: task-required block justification |
| `Policy`, rendered contract and identity | `extensions.replay`: counterfactual checks and exemptions |
| `Domain`, `Invocation`, `run`, `Execution` | `extensions.engine`: reusable worker pool |
| Reports, failure labels, presentation | `extensions.telemetry`: OpenTelemetry adaptation |

SQLite is an adapter; MCP and provider loops are integrations. None is needed to
understand or run the in-memory pattern below. `Engine` is also the internal pool
implementation used by a plain invocation; pool reuse is the optional behavior.

## Running a domain

```python
from contextlib import contextmanager
from pydantic import model_validator
from agent_pathcheck import (
    Domain, Invocation, Operation, PolicyModel, PolicyViolation, run,
    bind_operations, current_evidence, validate_model_output,
)

class Read(PolicyModel):
    pass

class Number(PolicyModel):
    value: int

class Total(PolicyModel):
    value: int

    @model_validator(mode="after")
    def check_total(self):
        rows = current_evidence().require(Read())
        if self.value != sum(row.value for row in rows):
            raise PolicyViolation("arithmetic", "total.sum", "Total must match observed numbers")
        return self

@contextmanager
def open_invocation(report):
    query = bind_operations([
        Operation(Read, Number, lambda request: [{"value": 2}, {"value": 4}]),
    ], report["policy_calls"], report["policy_events"])
    yield Invocation(
        inputs={}, functions={"query": query}, constructors={"Read": Read, "Total": Total},
        validate_result=lambda value: validate_model_output(value, Total, report["policy_events"]),
    )

domain = Domain(context={"unit": "items"}, open_invocation=open_invocation)
execution = run(
    "rows = query(Read())\nTotal(value=sum(row.value for row in rows))", domain=domain,
)
assert execution.report["contract_check"] == "passed"
assert execution.report["result"] == {"value": 6}
```

`run(code, domain=...)` creates and closes its own pool. Import
`Engine` explicitly from `agent_pathcheck.extensions.engine` to reuse a pool. The context managers
own cleanup. Startup, execution, validation, and cleanup failures cannot publish
a checked result. `Execution.value` is the unverified host return value;
`Execution.evidence` is host-only. Neither is exposed to the guest.

## Evidence and operations

`Operation(request_type, result_type, execute, observe=None, many=True,
required=True)` registers an exact request type. The dispatcher validates the
entire result batch and cardinality, commits the ledger, then calls the optional
domain observer. Empty batches count as completed. Rejected batches never
partially populate evidence. Observers can maintain domain indexes if needed.

`current_evidence()` offers `completed(request)`, `results(request)`,
`find(request_type, **fields)`, and `require(request)`. The last raises a structured
prerequisite violation. Keys preserve nested field types, including nonhashable
lists and dictionaries. Stored values and returned snapshots are isolated from
host mutation. The context is reset even on failure and remains active during
final validation. Returning an earlier model cannot skip final revalidation.

## Policy identity and advertised contracts

`Policy(name, version, build, output_model, parameters, contract, required_requests=None)` holds trusted
configuration. `current_policy()` gives validators access to it. A `Domain` with
a policy uses its `build` context manager; when `Domain.initialize` is supplied,
the factory receives `(report, initialize)`.

`render_contract(operations, constructors, inputs, extra_sources=())` renders
actual static class/validator source, dynamic fields, typed `Input` bindings and
operation cardinality. Use the same registry for rendering and dispatch.
`tool_description` adds effective limits and a glossary drawn from the report
schema. The MCP and agent-loop tool name is `run_workflow`, shared by both examples.


The SHA-256 of version plus rendered contract appears in both report context and
checked answers. Change the version when behavior outside the rendered source
changes. A domain may supply a data fingerprint hook. Each report also includes
the submitted code hash. These identify the declared inputs; they do not
authenticate an upstream database.

## Reports, failures, and tracing

`ExecutionReport` emits schema version 2. `read_report` retains version 1 and
unversioned historical verdicts without upgrading their replay guarantees or
inventing an ordering for old event lists. The public report is the schema's
JSON-compatible dump; domain-specific fields are allowed.

`timeline` is ordered by contiguous sequence number. It records constructor
verdicts, operation starts and completions; `policy_calls` and `policy_events`
provide convenient views. Operation entries include their eventual result check.
Caught rejections remain visible but do not invalidate a later valid result.

`PolicyViolation(kind, rule_id, message, entity=..., details=...)` preserves stable
labels through Pydantic. Built-in literal errors are scope violations; remaining
built-in field errors are schema violations. `failure.class` distinguishes
syntax, runtime, policy, resource_limit, data_block, contract and infrastructure.
Limits report their names and configured values. No suspension usage count is
claimed: the pinned SDK does not expose one.

For optional telemetry, pass `span_exporter=callback` to `run`, or adapt an
OpenTelemetry tracer with `extensions.telemetry.opentelemetry_exporter(tracer)`. Each constructor
and operation emits a timed span. Exporter failures do not change verdicts.

`validated_fields(report)` presents only host-checked results. An independent
`answer_check` cannot override a domain contract. Domains that deliberately have
no semantic output contract use `has_output_contract=False`; a fixture grader can
check their raw output separately.

## Counterfactuals and justified refusal

`Replay(name, perturb, exemption=None, observe=None)` from
`agent_pathcheck.extensions.replay` transforms a trusted domain initializer. With
`run(..., replays=[...], require_replays=True)`, the same code runs in fresh
sessions against each perturbed dataset. Every replay must pass or have an explicit host exemption to publish a
validated answer when required. `contract_check` retains the base verdict; `replay_check` records
the replay verdict. Advisory replays do not gate publication. Unchanged answers are `inconclusive`, retaining an `unchanged_answer` warning.
An optional `observe(result)` selects the signal being tested, such as ordered IDs;
`result_changed` describes the complete result and `signal_changed` the selected signal.
An `exemption(domain, base_execution, alternate_execution)` callback may return a
nonempty reason only for an inconclusive trial. It must derive that reason from
trusted evidence and policy. Failed trials cannot be exempted. The aggregate verdict
remains `inconclusive` even when exemptions permit publication. Perturbations are host-owned and must
preserve solvability; replay is evidence against stale constants, not a proof of
data dependence. No replay calls the model.

`Domain.allow_abstain=True` opts into the `Abstain(kind, rule_id, detail)`
constructor from `agent_pathcheck.extensions.abstain`. The final result is accepted
only if a matching unresolved data, resource, or schema block was recorded on an
exact request returned by `Policy.required_requests(parameters)`. The request type
and every scope value must match. An absent declaration cannot authorize refusal.
Constructor errors, optional operations, recovered queries, and deliberately
exhausting the SQL call budget cannot authorize refusal. Domains can add a stricter
`check_abstention`. A checked refusal has `result.kind="abstained"` and is distinct
from a valid empty domain result.

## Integrations and verification

`integrations.agent.run_loop(..., provider="openai" | "anthropic")` accepts an
asynchronous provider client and an MCP client. Both providers share exact tool
argument checks, bounded turns/calls, and clearing of prior validation on a new
attempt. Install the provider SDK in the calling application; the adapters only
translate the wire format. Model prose remains unverified.

`extensions.sqlalchemy.result_model` generates strict fields from Core columns,
including nullability. Explicit refinements supply domain constraints.
`request_model` binds every request column to host-owned literal scope values.
Unknown types/nullability and incomplete scope fail explicitly. FinOps uses these helpers for candidate/evidence rows and scoped requests, with
explicit bounds and literals. Business validators are never inferred from table metadata.

```bash
python -m unittest discover -s tests -v
python -m unittest discover -s examples/sql_math_tracer -v
python -m unittest discover -s examples/aws_finops -v
python -m examples.aws_finops.benchmark --replays
```

To replay your own saved submissions, supply a trusted manifest as described in
[the benchmark guide](../examples/aws_finops/BENCHMARK.md#replay-saved-submissions).
No experiment artifacts are bundled with this reference.

Typed actions, dbt generation, and direct persistent Code Mode integration remain
outside this implementation.

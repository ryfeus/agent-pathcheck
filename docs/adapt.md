# Adapt the pattern to your domain

Start with [Level 0](../examples/minimal/README.md), then add only the capabilities
your domain needs. Keep host data access, policy configuration, and final validation
outside guest control. The [claims table](claims.md) shows the boundaries to preserve.

The [online store walkthrough](../examples/online_store/README.md) applies these
steps across ten scenarios, including shared customer concepts and a bounded
planner that compiles registered concepts into these same runtime interfaces.

## 1. Bind request scope in types

Use literal fields for fixed scope. When policy changes scope, build the request
class on the host, as FinOps does:

```python
request = create_model("CostQuery", __base__=PolicyModel,
                       account=(Literal[account], ...),
                       month=(Literal[months], ...),
                       metric=(Literal["NetAmortizedCost"], ...),
                       group_by=(Literal["SERVICE"], ...))
```

Here `account` and `months` are trusted policy parameters. An agent chooses among
allowed requests; it cannot enlarge their permitted scope.

## 2. Validate the returned rows

Begin with explicit strict models from Level 0:

```python
class Number(PolicyModel):
    value: int
```

`PolicyModel` forbids extras, coercion, nonfinite floats, and unchecked reused
instances. FinOps optionally generates field boilerplate from SQLAlchemy columns,
then supplies explicit bounds and literals. Database types do not infer business rules.

## 3. Bind a host operation

An operation maps an exact request type to a validated result batch:

```python
query = bind_operations([
    Operation(Read, Number, lambda request: [{"value": value} for value in values]),
], report["policy_calls"], report["policy_events"])
```

Replace the handler with a bounded SQL, API, or file read. The dispatcher validates
the entire batch before committing evidence. Keep credentials, connection objects,
and independent expected answers out of guest bindings. An observer runs only
after a validated batch has committed.

## 4. Express semantic rules against evidence

FinOps obtains a scoped request from the current policy before reading the ledger:

```python
params = current_policy().parameters
request = params["models"][name](account=params["account"], **fields)
if not current_evidence().completed(request):
    raise PolicyViolation("prerequisite", rule_id, message)
rows = current_evidence().results(request)
```

Use stable rule IDs and meaningful violation kinds. Keep these functions small:
the renderer advertises their source to the agent, even though they execute on the host.

## 5. Recompute the final output

Level 0 accepts a total only when it agrees with observed rows:

```python
@model_validator(mode="after")
def check_total(self):
    rows = current_evidence().require(Read())
    if self.value != sum(row.value for row in rows):
        raise PolicyViolation("arithmetic", "total.sum", "Total must match observed numbers")
    return self
```

Bind `validate_model_output(value, Total, report["policy_events"])` as the final
validator. This rechecks an earlier constructed model against current evidence.
FinOps additionally recomputes eligibility, one-per-instance selection, ordering,
and totals. A schema-shaped dictionary is never itself an approval.

## 6. Wire the domain, then add integrations

Level 0 returns `Domain(context={"unit": "items"}, open_invocation=open_invocation)`.
Run it with `run(WORKFLOW, domain=make_domain())`. For configurable policy, use the
same factory and the same operation registry for rendering and execution:

```python
policy = Policy("numbers", "1", open_invocation, Total,
                parameters={"unit": "items"}, contract=contract)
domain = Domain({"unit": "items"}, open_invocation, policy=policy)
execution = run(code, domain=domain)
```

`contract` is the output of `render_contract(operations, constructors, inputs)`.
Use `validated_fields(execution.report)` for presentation. To add MCP, wrap that
runner with `create_code_server("numbers", tool_description(prose, contract), runner)`
from the shared integrations; its default tool is `run_workflow(code: str)`.
The [FinOps server](../examples/aws_finops/mcp_server.py) is the complete wiring example.

Extensions stay explicit: import pooling, replay, abstention, telemetry, or SQLAlchemy
generation from their `agent_pathcheck.extensions` modules. If enabling abstention,
declare `Policy.required_requests(parameters)` with exact task dependencies. If
requiring replay, supply meaningful perturbations and justify any inconclusive
exemption from trusted evidence. Never use an exemption to waive a failed trial.

## Before copying this into an application

- Can an agent ask out of scope, including through edited guest fields?
- Can it claim an answer without a completed, validated evidence batch?
- Can a stale model skip final revalidation?
- Can an unrelated failed request authorize abstention on a solvable task?
- Do unchanged replays clearly report their lack of signal?
- Are the source data and business rules actually trustworthy and complete?

Use the regression tests linked in [claims](claims.md) as concrete starting points.

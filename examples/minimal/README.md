# Level 0: evidence before claims

This is the complete in-memory pattern: a typed request, a host-validated result
batch, and an output validator that recomputes the total from private evidence.
There is no database, model API, MCP transport, or network access at runtime.
The [claims table](../../docs/claims.md) links the guarantees to executable tests.

From the repository root, using Python 3.12:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r examples/minimal/requirements.txt
python -m examples.minimal
python -m unittest discover -s tests -p test_minimal.py -v
```

The checked result is `{"value": 6}`, with the unit `items`. Read
[workflow.py](workflow.py) from top to bottom. `Read` is the only request;
`Number` validates every returned row; `Total` requires a completed read and
recomputes its sum. The final value is revalidated before publication.

Try returning `Total(value=6)` without a read: it fails for missing evidence.
Query and then return `Total(value=7)`: it fails arithmetic validation. An empty
completed batch supports a total of zero. An invalid batch contributes no evidence.

A coincidentally correct constant can still pass after the required read. This
checks the answer against observations; it does not prove the program's derivation.
Continue with [Level 1: sales](../sql_math_tracer/README.md) to add SQL and scoped
requests, or the [adaptation guide](../../docs/adapt.md) to build your own domain.

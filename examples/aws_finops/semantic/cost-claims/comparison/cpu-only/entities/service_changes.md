---
anchor: "examples.aws_finops.policy:ServiceChanges"
kind: "entity"
---
# ServiceChanges

<!-- generated:start -->
Field `before`: `CostQuery`; required; constraints `[]`.
Field `after`: `CostQuery`; required; constraints `[]`.
Field `top`: `int | None`; optional; constraints `[Gt(gt=0)]`.
Field `rows`: `list[ServiceChange] | None`; optional; constraints `[]`.

```python
class ServiceChanges(Claim):
    before: CostQuery
    after: CostQuery
    top: int | None = Field(default=None, gt=0)
    rows: list[ServiceChange] | None = None

    @model_validator(mode='before')
    @classmethod
    def check(cls, data):
        data = claim_input(data)
        if not isinstance(data, dict) or not {'before', 'after'} <= data.keys():
            return data
        top = data.get('top')
        if top is not None and (type(top) is not int or top <= 0):
            return data  # Strict field validation reports invalid selection sizes.
        before = CostQuery.model_validate(data['before'])
        after = CostQuery.model_validate(data['after'])
        old = {row.service: row.cost_cents for row in current_evidence().require(before)}
        new = {row.service: row.cost_cents for row in current_evidence().require(after)}
        changes = [(service, new.get(service, 0) - old.get(service, 0)) for service in old.keys() | new.keys()]
        changes.sort(key=lambda item: (-item[1], item[0]))
        actual = [dict(service=service, change_cents=value) for service, value in changes[:top]]
        if isinstance(data.get('rows'), list):
            data = {**data, 'rows': [row.model_dump() if isinstance(row, ServiceChange) else row
                                     for row in data['rows']]}
        return settle(data, actual, 'claims.service_changes', field='rows')
```

Rules: [claims.service_changes](../rules/claims.service_changes.md)
<!-- generated:end -->

## Meaning

Changes include missing services as zero, ordered by signed change descending and service name ascending. An omitted top selects every service. A positive top selects up to that many services.

## Notes (advice, not checked)

Documentation explains the policy; reading it does not complete an evidence request.

## Rationale

See the [required-claim rule](../rules/claims.required_missing.md) for the checked boundary.

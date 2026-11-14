---
anchor: "examples.aws_finops.policy:Answer"
kind: "entity"
---
# Answer

<!-- generated:start -->
Field `claims`: `dict[str, CostTotal | CostDelta | ServiceChanges]`; required; constraints `[]`.
Field `text`: `str`; required; constraints `[]`.

```python
class Answer(AnswerEnvelope):
    claims: dict[str, CostTotal | CostDelta | ServiceChanges]
    text: str
```
<!-- generated:end -->

## Meaning

This question requires the claims listed in the semantic index. Claim keys are chosen by the workflow. Text substitutes checked values through simple named placeholders and contains no literal digits.

## Notes (advice, not checked)

Documentation explains the policy; reading it does not complete an evidence request.

## Rationale

See the [required-claim rule](../rules/claims.required_missing.md) for the checked boundary.

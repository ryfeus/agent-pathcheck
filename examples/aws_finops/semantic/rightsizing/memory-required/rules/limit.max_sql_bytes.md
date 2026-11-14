---
anchor: "rule:limit.max_sql_bytes"
kind: "rule"
level: "checked"
owner: "reference-example"
---
# limit.max_sql_bytes

<!-- generated:start -->
Execution respects the max_sql_bytes budget

Level: checked

Owner: reference-example

Source: engine contract

The host checks this boundary before publishing a validated answer.

References: [Active policy and operations](../index.md)
<!-- generated:end -->

## Meaning

Execution respects the max_sql_bytes budget.

## Notes (advice, not checked)

The maturity label describes the available enforcement and regression evidence.

## Rationale

The host checks this boundary before publishing a validated answer. See [limit.max_sql_bytes](limit.max_sql_bytes.md).

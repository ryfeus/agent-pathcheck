---
anchor: "examples.aws_finops.policy:cpu-only:rules"
kind: "index"
---
# Rules

<!-- generated:start -->
- [abstain.unjustified](abstain.unjustified.md)
- [claim.attempt_limit](claim.attempt_limit.md)
- [claims.cost_delta](claims.cost_delta.md)
- [claims.cost_total](claims.cost_total.md)
- [claims.empty](claims.empty.md)
- [claims.rejection_limit](claims.rejection_limit.md)
- [claims.required_missing](claims.required_missing.md)
- [claims.service_changes](claims.service_changes.md)
- [claims.text_numbers](claims.text_numbers.md)
- [claims.text_placeholder](claims.text_placeholder.md)
- [data.integrity](data.integrity.md)
- [evidence.required](evidence.required.md)
- [limit.max_constructions](limit.max_constructions.md)
- [limit.max_feed_duration_secs](limit.max_feed_duration_secs.md)
- [limit.max_memory](limit.max_memory.md)
- [limit.max_queries](limit.max_queries.md)
- [limit.max_rows](limit.max_rows.md)
- [limit.max_source_chars](limit.max_source_chars.md)
- [limit.max_sql_bytes](limit.max_sql_bytes.md)
- [limit.max_sql_value_bytes](limit.max_sql_value_bytes.md)
- [limit.max_suspensions](limit.max_suspensions.md)
- [limit.max_total_sleep_secs](limit.max_total_sleep_secs.md)
- [limit.sql_instructions](limit.sql_instructions.md)
- [operation.single_result](operation.single_result.md)
- [pydantic.extra_forbidden](pydantic.extra_forbidden.md)
- [pydantic.finite_number](pydantic.finite_number.md)
- [pydantic.float_type](pydantic.float_type.md)
- [pydantic.greater_than](pydantic.greater_than.md)
- [pydantic.greater_than_equal](pydantic.greater_than_equal.md)
- [pydantic.int_type](pydantic.int_type.md)
- [pydantic.less_than_equal](pydantic.less_than_equal.md)
- [pydantic.list_type](pydantic.list_type.md)
- [pydantic.literal_error](pydantic.literal_error.md)
- [pydantic.missing](pydantic.missing.md)
- [pydantic.model_type](pydantic.model_type.md)
- [pydantic.string_type](pydantic.string_type.md)
- [replay.required](replay.required.md)
<!-- generated:end -->

## Meaning

This page describes the active host contract for scoped answer claims. Only completed query evidence can support their computed values.

## Notes (advice, not checked)

Documentation explains the policy; reading it does not complete an evidence request.

## Rationale

See the [required-claim rule](claims.required_missing.md) for the checked boundary.

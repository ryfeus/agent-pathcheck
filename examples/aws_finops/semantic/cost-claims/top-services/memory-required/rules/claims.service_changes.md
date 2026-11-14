---
anchor: "rule:claims.service_changes"
kind: "rule"
level: "tested"
owner: "reference-example"
---
# claims.service_changes

<!-- generated:start -->
Compute and order the scoped service changes

Level: tested

Owner: reference-example

Source: claims contract

Missing services count as zero; signed changes descend and service names break ties. An oversized top returns all available services.

References: [ServiceChanges](../entities/service_changes.md)
<!-- generated:end -->

## Meaning

This page describes the active host contract for scoped answer claims. Only completed query evidence can support their computed values.

## Notes (advice, not checked)

Documentation explains the policy; reading it does not complete an evidence request.

## Rationale

See the [executable rule](claims.service_changes.md) for the checked boundary.

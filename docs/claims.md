# Claims backed by tests

These are executable checks of the encoded rules, not promises about every
possible domain. Run the offline suites from the root README. CI also
checks that every linked test below still exists.

“Answer claims” below refers to the optional [composable output types](composable-claims.md).

| Claim | Proven by |
| --- | --- |
| Level 0 recomputes a total from observed rows; a completed empty batch supports zero. | [`MinimalTests.test_observed_sum_and_empty_batch`](../tests/test_minimal.py) |
| Missing evidence and incorrect arithmetic cannot produce a checked Level 0 answer. | [`MinimalTests.test_missing_evidence_and_wrong_arithmetic_cannot_publish`](../tests/test_minimal.py) |
| A rejected batch never partially becomes evidence; an empty batch counts as completed. | [`EngineTests.test_batch_commit_is_atomic_and_empty_is_completed`](../tests/test_engine_improvements.py) |
| The final output is revalidated against current observations. | [`CoreTests.test_final_value_is_revalidated_after_later_observations`](../tests/test_core.py) |
| Pool reuse preserves fresh guest state and private evidence. | [`EngineTests.test_reused_pool_has_fresh_guest_and_evidence`](../tests/test_engine_improvements.py) |
| Host and guest mutation cannot alter stored evidence snapshots. | [`EngineTests.test_nonhashable_request_fields_and_mutation_isolation`](../tests/test_engine_improvements.py), [`BoundaryAttackTests.test_mutating_guest_evidence_does_not_change_host_observations`](../examples/aws_finops/test_adversarial.py) |
| Invalid scopes and untyped requests fail before SQL runs. | [`PolicyTests.test_invalid_constructions_and_untyped_calls_never_query_database`](../examples/sql_math_tracer/test_policy.py) |
| Invented query classes and constructor shortcuts cannot reach typed SQL. | [`BoundaryAttackTests.test_invented_query_types_and_constructor_shortcuts_never_reach_sql`](../examples/aws_finops/test_adversarial.py) |
| Editing guest request fields cannot expand host scope. | [`BoundaryAttackTests.test_guest_request_fields_cannot_change_host_account_or_metric`](../examples/aws_finops/test_adversarial.py) |
| The SQLite adapter rejects writes and recovers for later reads. | [`TracerTests.test_read_only_protection_and_recovery`](../examples/sql_math_tracer/test_tracer.py) |
| A sales scope-policy change requires a different workflow. | [`PolicyTests.test_same_workflow_rejected_after_policy_change_then_repaired`](../examples/sql_math_tracer/test_policy.py) |
| Sales arithmetic is checked by an independent fixture answer. | [`PolicyTests.test_policy_acceptance_is_separate_from_numeric_correctness`](../examples/sql_math_tracer/test_policy.py) |
| FinOps validates every service delta and the total without its fixture oracle. | [`SemanticTests.test_cost_fabrications_fail_without_an_answer_oracle`](../examples/aws_finops/test_semantics.py) |
| FinOps requires the exact eligible selection, ordering, and observed savings total. | [`SemanticTests.test_plans_require_exact_top_selection_and_observed_total`](../examples/aws_finops/test_semantics.py) |
| FinOps chooses at most one eligible recommendation per instance. | [`SemanticTests.test_reference_workflow_selects_one_eligible_recommendation_per_instance`](../examples/aws_finops/test_semantics.py) |
| A valid empty plan requires complete queries and zero savings. | [`SemanticTests.test_no_eligible_outcome_requires_complete_queries_and_zero_savings`](../examples/aws_finops/test_semantics.py) |
| A matching fabricated oracle cannot override a failed domain contract. | [`SemanticTests.test_matching_fabricated_oracle_cannot_override_contract_failure`](../examples/aws_finops/test_semantics.py) |
| Fake outputs and approval flags cannot authorize proposals. | [`BoundaryAttackTests.test_fabricated_flags_values_and_fake_outputs_cannot_authorize_proposals`](../examples/aws_finops/test_adversarial.py) |
| Hardcoded totals that pass the base fixture fail required arithmetic replay. | [`FinOpsImprovementTests.test_constants_pass_base_but_fail_required_replay`](../tests/test_engine_improvements.py) |
| Hardcoded recommendation IDs fail required ranking replay even when their total is recomputed. | [`ReplayTests.test_hardcoded_selection_fails_required_ranking_replay`](../tests/test_reference_architecture.py) |
| An unchanged replay requires an explicit host exemption before required publication. | [`ReplayTests.test_unchanged_requires_explicit_host_exemption`](../tests/test_reference_architecture.py) |
| Failed replay cannot receive an exemption. | [`ReplayTests.test_failed_replays_cannot_be_exempted`](../tests/test_reference_architecture.py) |
| An unrelated blocked request cannot authorize abstention. | [`AbstentionTests.test_unneeded_request_block_cannot_authorize_abstention`](../tests/test_reference_architecture.py) |
| Optional or recovered blocks cannot authorize refusal; all five blocked fixtures support justified refusal. | [`EngineTests.test_optional_or_recovered_blocks_do_not_justify_refusal`](../tests/test_engine_improvements.py), [`FinOpsImprovementTests.test_all_five_blocked_cases_can_justify_abstention`](../tests/test_engine_improvements.py) |
| Policy messages mentioning memory limits remain policy failures. | [`FailureTests.test_memory_word_in_policy_error_does_not_make_it_a_resource_limit`](../tests/test_reference_architecture.py) |
| Rendered FinOps source excludes the known unsupported `next(generator)` pattern. | [`ContractTests.test_rendered_contract_avoids_unsupported_constructs`](../tests/test_reference_architecture.py) |
| Generated SQLAlchemy models preserve the independently pinned handwritten schemas. | [`SQLAlchemyTests.test_generated_domain_models_match_handwritten_schemas`](../tests/test_integrations.py) |
| Model prose never replaces a checked answer. | [`AgentTests.test_wrong_prose_cannot_replace_checked_result`](../examples/sql_math_tracer/test_agent.py) |
| A malformed or failed later attempt clears prior validation. | [`AgentTests.test_new_malformed_attempt_clears_prior_validation`](../examples/sql_math_tracer/test_agent.py), [`AgentTests.test_last_failed_result_cannot_inherit_a_previous_pass`](../examples/sql_math_tracer/test_agent.py) |
| Cleanup failure cannot publish a checked result. | [`CoreTests.test_execution_failure_and_cleanup_failure_cannot_publish_a_result`](../tests/test_core.py) |
| Saved replay rejects unknown or changed artifacts before starting an engine. | [`ArtifactTests.test_all_inputs_are_verified_before_starting_an_engine`](../tests/test_artifact_replay.py), [`ArtifactTests.test_downloaded_path_matches_manifest_and_modified_bytes_fail`](../tests/test_artifact_replay.py) |
| Host-filled frozen answer claims survive guest construction and final revalidation. | [`ClaimsFeasibilityTests.test_guest_fill_and_final_revalidation`](../tests/test_claims_feasibility.py) |
| Nested claim instances and dictionaries reject forged or stale values against current evidence. | [`ClaimsFeasibilityTests.test_nested_instances_and_dicts_revalidate_current_evidence`](../tests/test_claims_feasibility.py) |
| Answer claims require completed scoped evidence and reject forged inner values. | [`ComposableClaimTests.test_prerequisite_scope_and_forged_nested_claim`](../tests/test_composable_claims.py) |
| Answer templates reject unknown or complex placeholders and literal digits. | [`ComposableClaimTests.test_text_rules`](../tests/test_composable_claims.py) |
| A caught answer-claim rejection remains fatal under the opt-in rejection budget. | [`ComposableClaimTests.test_rejections_latch_and_legacy_default`](../tests/test_composable_claims.py) |
| Composed cost answers match every solvable historical cost oracle and preserve the row-limit block. | [`FinOpsClaimsTests.test_equivalence_all_cost_scenarios`](../examples/aws_finops/test_claims.py) |
| Required claims match source periods and selection qualifiers; extra claims are allowed. | [`FinOpsClaimsTests.test_required_claims_match_source_and_qualifiers`](../examples/aws_finops/test_claims.py) |
| Host presentation carries source scope and formats large and negative cents exactly. | [`FinOpsClaimsTests.test_rendered_answer_preserves_scope_and_exact_money`](../examples/aws_finops/test_claims.py) |
| Host-filled claims adapt to replay; stale supplied constants cannot publish a required-replay answer. | [`ClaimsIntegrationTests.test_host_filled_adapts_and_supplied_constants_fail_replay`](../examples/aws_finops/test_claims_integration.py) |
| A second question uses the same output schema and evidence-backed replay exemptions. | [`ClaimsIntegrationTests.test_second_question_reuses_output_schema_and_handles_unchanged_replay`](../examples/aws_finops/test_claims_integration.py) |
| Claims MCP discovery and captured error hints contain no fixture-oracle values under the provenance-aware hygiene checks. | [`ClaimsMCPTests.test_discovery_lookup_and_host_presentation`](../examples/aws_finops/test_claims_integration.py), [`ClaimsIntegrationTests.test_all_guest_error_hints_are_oracle_free`](../examples/aws_finops/test_claims_integration.py) |

## Online store

The [store walkthrough](../examples/online_store/README.md) applies the same
runtime boundaries to one shared domain and a bounded example-local planner.

| Claim | Proven by |
| --- | --- |
| Store-local month boundaries and return-date accounting determine revenue. | [`StoreTests.test_month_edges_and_refund_event_dates`](../examples/online_store/test_store.py) |
| Refund decisions bind the actual customer, line, delivery, remaining payment and quantity. | [`StoreTests.test_refund_exact_entity_dates_amount_and_quantity_boundaries`](../examples/online_store/test_store.py) |
| Restocking rejects ineligible products, borrowed history, duplicate or misordered selections. | [`StoreTests.test_restock_disqualifiers_sibling_history_and_ranking`](../examples/online_store/test_store.py) |
| One activity-window change updates both customer policies and their identities. | [`StoreTests.test_shared_active_window_changes_both_customer_policies_and_identity`](../examples/online_store/test_store.py) |
| Missing optional or recovered evidence does not justify refusal; partial returns cannot support a rate. | [`StoreTests.test_abstention_requires_unresolved_exact_required_block`](../examples/online_store/test_store.py), [`StoreTests.test_returns_truncation_zero_sales_and_mismatched_cohort`](../examples/online_store/test_store.py) |
| The correction invalidates the old selection, and its negative probe detects weakened enforcement. | [`StoreTests.test_correction_and_weakened_rule_negative_probe`](../examples/online_store/test_store.py) |
| Promotion claims require the declining-category join and reject forged nested rows. | [`CompositionTests.test_nested_forgery_scope_omission_and_caught_claim_rejection`](../examples/online_store/test_composition.py) |
| Multiple registered compositions reuse one validator; plans cannot override inherited rules. | [`CompositionTests.test_compiler_supports_multiple_compositions_without_new_validators`](../examples/online_store/test_composition.py), [`CompositionTests.test_plans_cannot_weaken_inherited_rules_or_add_executable_code`](../examples/online_store/test_composition.py) |
| Historical Gold filters, overdue undelivered orders and supplier deduplication are explicit. | [`CompositionTests.test_quarter_end_gold_undelivered_sla_and_supplier_deduplication`](../examples/online_store/test_composition.py) |
| Planning failures never launch execution; a later failed workflow clears publication. | [`StoreMCPTests.test_planning_failure_never_launches_execution`](../examples/online_store/test_mcp.py), [`StoreMCPTests.test_repair_and_later_failure_control_publication`](../examples/online_store/test_mcp.py) |

## Not claimed

- **Derivation:** a correct constant can pass after the necessary observations.
  Replay detects particular stale constants and selections; it is not a general
  proof that code used the data to derive its answer.
- **Data authenticity:** the host trusts its data source. Hashes identify retained
  bytes and declared policy, not the truth of upstream facts.
- **Unencoded rules:** no validator enforces a business requirement that nobody
  supplied. Coverage and thresholds here describe synthetic examples.
- **Prose accuracy:** narrative explanations and business causes remain unverified.
- **Planner interpretation:** a checked composed answer follows its displayed
  normalized plan; this does not prove that the plan captures every intent of an
  unrestricted natural-language question.
- **Universal sandbox compatibility:** the source guard covers a documented known
  incompatibility; it is not a complete Monty static analyzer.

"""Explicit rationale for the reference's executable decisions; no fixture answers."""

from agent_pathcheck.rules import Level, Rule, shared_rules


DESCRIPTIONS = {
    'costs.month_required': ('Complete both scoped monthly reads', 'A comparison requires observations of both periods, including empty batches.'),
    'costs.service_changes': ('Include and order every observed service change', 'Services that appear or disappear still contribute to the comparison.'),
    'costs.total': ('Sum the observed changes in integer cents', 'Exact arithmetic keeps the claimed total consistent with the detailed changes.'),
    'rightsizing.candidates_required': ('Discover candidates before selecting recommendations', 'Only recommendations returned by the scoped host query are selectable.'),
    'rightsizing.discovered_candidate': ('Select a discovered recommendation', 'An identifier alone does not establish a recommendation or its properties.'),
    'rightsizing.architecture': ('Preserve the observed CPU architecture', 'The target catalog grounds compatibility; this example does not evaluate application migration.'),
    'rightsizing.risk': ('Respect the performance risk threshold', 'Estimated savings do not override the configured risk constraint.'),
    'rightsizing.positive_savings': ('Require positive estimated savings', 'A resize must reduce estimated cost to be eligible.'),
    'rightsizing.metric_required': ('Read every metric required by the active policy', 'Metric requirements belong to the policy and cannot be chosen by the workflow.'),
    'rightsizing.instance_evidence': ('Require evidence for each selected instance', 'Missing memory collection is not evidence of low memory utilization.'),
    'rightsizing.coverage': ('Require sufficient observation coverage', 'Coverage measures collected days, not freshness or recency.'),
    'rightsizing.utilization': ('Respect each metric utilization threshold', 'Eligibility requires observed utilization within the configured bounds.'),
    'rightsizing.unique_instance': ('Select at most one resize for each instance', 'Alternative recommendations are competing choices, not additive savings.'),
    'rightsizing.maximum_proposals': ('Respect the proposal count limit', 'The requested plan has a bounded number of recommendations.'),
    'rightsizing.top_candidates': ('Select the best eligible recommendations in deterministic order', 'Savings rank eligible choices; recommendation identifiers break ties.'),
    'rightsizing.outcome': ('Distinguish an empty eligible set from a recommendation plan', 'An empty answer is meaningful only after all required evidence is observed.'),
    'rightsizing.total': ('Sum savings from the selected observed candidates', 'The total must use the same recommendations that the plan presents.'),
}
TESTED = {'costs.total', 'costs.service_changes', 'rightsizing.metric_required',
          'rightsizing.unique_instance', 'rightsizing.top_candidates', 'rightsizing.outcome', 'rightsizing.total'}

CLAIM_RULES = {
    'claims.cost_total': ('Total the scoped observed costs', 'An omitted value is filled; a supplied value must match the completed request.'),
    'claims.cost_delta': ('Subtract the earlier scoped total from the later total', 'Both nested totals are revalidated against current evidence.'),
    'claims.service_changes': ('Compute and order the scoped service changes', 'Missing services count as zero; signed changes descend and service names break ties. An oversized top returns all available services.'),
    'claims.empty': ('Include answer claims', 'An empty collection does not provide a checked descriptive answer.'),
    'claims.text_placeholder': ('Reference claims with simple named placeholders', 'Attribute access, indexing, conversions, format specifications and unknown names are rejected.'),
    'claims.text_numbers': ('Keep literal digits out of answer prose', 'The host substitutes checked values; Unicode digits are also rejected outside placeholders.'),
    'claims.required_missing': ('Include every question-required source and qualifier', 'Claim types alone cannot prevent omission or selection of a different period.'),
    'claims.rejection_limit': ('A rejected answer claim permanently fails this invocation', 'Catching a constructor error cannot turn guessing into a publishable answer. Submit a new workflow to repair it.'),
    'pydantic.greater_than': ('Selection sizes must be positive', 'Use an omitted top for every service, or a positive integer for a ranked prefix.'),
}


def rules_for(case, mode=None):
    rules = shared_rules()
    if case == 'cost-claims':
        rules.update({key: Rule(key, summary, Level.TESTED, 'reference-example', 'claims contract', why)
                      for key, (summary, why) in CLAIM_RULES.items()})
    for key, (summary, why) in DESCRIPTIONS.items():
        if key.startswith(case + '.'):
            rules[key] = Rule(key, summary, Level.TESTED if key in TESTED else Level.CHECKED,
                              'reference-example', 'fixture design', why)
    if mode == 'costs-with-caveats':
        rules['costs.service_churn'] = Rule('costs.service_churn', 'Disclose service churn', Level.GUIDED,
            'reference-example', 'fixture design',
            'New or removed services may reflect migrations or one-off changes rather than usage.')
        rules['costs.caveats_required'] = Rule('costs.caveats_required', 'Carry exactly the required caveats', Level.TESTED,
            'reference-example', 'fixture design', 'Required caveats must survive into the checked answer.')
    if mode == 'memory-required-min-savings':
        rules['rightsizing.min_savings'] = Rule('rightsizing.min_savings', 'Require the agreed minimum estimated savings',
            Level.TESTED, 'reference-example', 'correction:min-savings',
            'Small savings may not justify change risk. The configured floor distinguishes marginal resizes from material savings in the reference scenarios.')
    return rules

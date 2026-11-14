"""Shared eligibility and provenance; fixture values are not policy."""

from agent_pathcheck import DataBlocked
from agent_pathcheck.rules import Level, Rule, shared_rules
from .concepts import local_date


def restock_reason(row, config):
    if row.product_status != "active":
        return "product_inactive"
    if row.supplier_status != "active":
        return "supplier_inactive"
    if row.history_days < config.history_days:
        return "history"
    if row.units_30 == 0:
        return "zero_velocity"
    if row.monthly_sales_cents < config.minimum_monthly_sales_cents:
        return "monthly_sales"
    if row.monthly_sales_cents <= 0 or (row.monthly_cost_cents is not None and row.monthly_sales_cents - row.monthly_cost_cents <= 0):
        return "margin"
    if row.inventory_complete and row.on_hand is not None and row.reorder_point is not None:
        if row.on_hand >= row.reorder_point:
            return "sufficient_stock"
        if row.on_hand * 30 >= row.units_30 * row.lead_time_days:
            return "cover"
    else:
        raise DataBlocked("store.inventory_missing", "Complete inventory is required for potentially eligible products")
    if row.monthly_cost_cents is None:
        raise DataBlocked("store.cost_missing", "Cost is required for potentially eligible restock products")
    if row.lost_revenue_cents is None:
        raise DataBlocked("store.forecast_missing", "A dated forecast is required for each eligible product")
    return None


def refund_decision(rows, request, config):
    if len(rows) != 1:
        return "rejected", "line_not_found"
    row = rows[0]
    if row.order_id != request.order_id or row.customer_id != request.customer_id:
        return "rejected", "ownership"
    if row.delivered_at is None:
        return "rejected", "not_delivered"
    if request.requested_at < row.delivered_at:
        return "rejected", "before_delivery"
    age = (local_date(request.requested_at, config.timezone) - local_date(row.delivered_at, config.timezone)).days
    if age > config.refund_days:
        return "rejected", "window"
    if (request.refund_cents > row.paid_cents - row.refunded_cents
            or request.quantity > row.quantity - row.returned_quantity):
        return "rejected", "remaining_amount_or_quantity"
    if request.refund_cents > config.auto_refund_cents:
        return "human_review", "amount_over_limit"
    return "auto_approve", "within_policy"


DESCRIPTIONS = {
    "store.scope": ("All reads inherit production scope and store-local boundaries", "Finance", "Typed requests bind every scoped read before SQL."),
    "store.metric_result": ("Recompute the complete measure result", "Finance", "Observed rows determine totals, union comparisons and ordering."),
    "store.refund": ("Bind refunds to this customer, line and delivery", "Support", "Ownership, remaining payment, quantity, delivery window and review limit must all agree."),
    "store.restock_eligible": ("Require every restock eligibility condition", "Merchandising", "History belongs to this product; stock, cost and dated forecasts cannot be borrowed."),
    "store.restock_plan": ("Select the complete ordered restock ranking", "Merchandising", "Select one entry per product, sort forecast descending then product ID, and sum those forecasts."),
    "store.active_customer": ("Share one active-customer definition", "CRM", "A qualifying production order falls within the configured active-day window."),
    "store.gold": ("Share the rolling Gold definition", "CRM", "Gold requires an active customer and the configured twelve-month net revenue threshold."),
    "store.customers": ("Recompute the requested customer cohort", "CRM", "Loyalty compares snapshots; churn uses the same active window."),
    "store.cost_missing": ("Missing exact-product cost blocks margin", "Finance", "Other products cannot supply an unknown unit cost."),
    "store.margin": ("Margin uses observed net revenue and cost", "Finance", "Subtract costs of units sold in the same accounting period."),
    "store.return_rate": ("Return rate follows the order cohort", "Support", "Both counts refer to the same production order cohort and observation cutoff."),
    "store.pause_join": ("Pause candidates must belong to declining categories", "Merchandising", "Compare discount and realized margin percentages over the promotion overlap, then verify the category join."),
    "store.promotion_margin": ("Promotion assessment requires a defined margin", "Finance", "Missing cost or nonpositive net revenue cannot support a margin-rate comparison."),
    "store.inventory_missing": ("Observe every known warehouse at the snapshot", "Ops", "Missing warehouse inventory cannot be treated as zero stock."),
    "store.forecast_missing": ("Bind forecasts to product and snapshot", "Merchandising", "The forecast is synthetic source data, not a formula certified by this policy."),
    "store.late_delivery": ("Count overdue shipments, including undelivered orders", "Ops", "Count a late order once per supplier present; the SLA uses local calendar days."),
    "store.composition": ("Compose only registered concepts with inherited rules", "reference-example", "Plans select capabilities; they cannot replace predicates, relationships or validators."),
}


def rules_for(config):
    rules = shared_rules()
    rules.update({key: Rule(key, summary, Level.TESTED, owner, "online-store-domain", why)
                  for key, (summary, owner, why) in DESCRIPTIONS.items()})
    rules["store.minimum_sales"] = Rule("store.minimum_sales", "Require the corrected monthly-sales floor",
        Level.TESTED if config.minimum_monthly_sales_cents else Level.DOCUMENTED, "Merchandising",
        "correction:minimum-monthly-sales", "The base policy records the correction; the corrected policy enforces its inclusive floor.")
    rules["store.local_supplier"] = Rule("store.local_supplier", "Prefer local suppliers", Level.DOCUMENTED,
        "Merchandising", "online-store-domain", "Regional supplier preference is advice and does not change eligibility.")
    rules["store.category_churn"] = Rule("store.category_churn", "Mention new and discontinued categories", Level.GUIDED,
        "Finance", "online-store-domain", "Narrative guidance does not certify a causal explanation.")
    return rules

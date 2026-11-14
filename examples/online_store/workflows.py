"""Reference guest programs. They query and calculate; they contain no answer oracles."""

from .policy import build_policy


def request_code(policy, index):
    name, fields = policy.parameters["required_specs"][index]
    return name + "(" + ", ".join(f"{key}={value!r}" for key, value in fields.items()) + ")"


def workflow(case="restock", policy="base", *, bundle=None, **options):
    bundle = bundle or build_policy(case, policy, **options)
    case = bundle.parameters["case"]
    requests = [request_code(bundle, i) for i in range(len(bundle.parameters["required_specs"]))]
    if case in ("revenue", "shipments", "category-comparison", "composed"):
        spec = bundle.parameters["measure_spec"]
        if spec["shape"] == "comparison":
            return f'''before = query({requests[0]})
after = query({requests[1]})
left = {{r.key: r.value for r in before}}
right = {{r.key: r.value for r in after}}
labels = {{r.key: r.label for r in before + after}}
keys = sorted(labels, key=lambda k: (-(right.get(k, 0) - left.get(k, 0)), labels[k], k))
rows = [MeasureRow(key=k, label=labels[k], value=right.get(k, 0)-left.get(k, 0),
                   before=left.get(k, 0), after=right.get(k, 0)) for k in keys]
MetricResult(rows=rows, total=sum(r.value for r in rows))'''
        ordering = "(-r.value, r.key)" if spec["shape"] == "top_k" else "(r.label, r.key)"
        selection = f"[:{spec['top']}]" if spec["shape"] == "top_k" else ""
        return f'''observations = query({requests[0]})
rows = sorted(observations, key=lambda r: {ordering}){selection}
MetricResult(rows=rows, total=sum(r.value for r in rows))'''
    if case == "restock":
        return f'''rows = query({requests[0]})
ordered = sorted(rows, key=lambda r: (-(r.lost_revenue_cents or 0), r.product_id))
proposals = []
total = 0
for row in ordered:
    try:
        proposal = RestockProposal(product_id=row.product_id)
    except ValueError:
        continue
    proposals.append(proposal)
    total += row.lost_revenue_cents
    if len(proposals) == store_config["restock_size"]:
        break
RestockPlan(proposals=proposals, total_lost_revenue_cents=total,
            outcome="recommendations" if proposals else "no_eligible_products")'''
    if case == "refund":
        return f'''rows = query({requests[0]})
decision = "rejected"
reason = "line_not_found"
if len(rows) == 1:
    row = rows[0]
    if row.order_id != refund_request["order_id"] or row.customer_id != refund_request["customer_id"]:
        reason = "ownership"
    elif row.delivered_at is None:
        reason = "not_delivered"
    elif refund_request["requested_at"] < row.delivered_at:
        reason = "before_delivery"
    elif row.delivery_age_days > store_config["refund_days"]:
        reason = "window"
    elif (refund_request["refund_cents"] > row.paid_cents - row.refunded_cents
          or refund_request["quantity"] > row.quantity - row.returned_quantity):
        reason = "remaining_amount_or_quantity"
    elif refund_request["refund_cents"] > store_config["auto_refund_cents"]:
        decision, reason = "human_review", "amount_over_limit"
    else:
        decision, reason = "auto_approve", "within_policy"
RefundDecision(decision=decision, reason=reason)'''
    if case == "loyalty":
        cutoffs = [scope["cutoff"] for _, scope in bundle.parameters["required_specs"]]
        return f'''before = query({requests[0]})
after = query({requests[1]})
old_window = customer_windows[{cutoffs[0]!r}]
new_window = customer_windows[{cutoffs[1]!r}]
old = [r.customer_id for r in before if r.last_order_at is not None and
       old_window["start"] <= r.last_order_at < old_window["end"] and r.net_revenue_cents >= store_config["gold_cents"]]
new = [r.customer_id for r in after if r.last_order_at is not None and
       new_window["start"] <= r.last_order_at < new_window["end"] and r.net_revenue_cents >= store_config["gold_cents"]]
CustomerList(customer_ids=sorted(cid for cid in new if cid not in old))'''
    if case == "churn":
        return f'''rows = query({requests[0]})
window = customer_windows["churn"]
selected = [r for r in rows if r.last_order_at is not None and window["start"] <= r.last_order_at < window["end"]]
CustomerList(customer_ids=[r.customer_id for r in sorted(selected, key=lambda r: (r.last_order_at, r.customer_id))])'''
    if case == "product-margin":
        return f'''try:
    row = query({requests[0]})
except ValueError:
    result = Abstain(kind="data_block", rule_id="store.cost_missing", detail="The required product cost is unavailable.")
else:
    result = ProductMargin(margin_cents=row.net_revenue_cents-row.cost_cents)
result'''
    if case == "return-rate":
        return f'''sold = query({requests[0]})
try:
    returned = query({requests[1]})
except ValueError:
    result = Abstain(kind="resource_limit", rule_id="limit.max_rows", detail="The required cohort returns exceeded the row limit.")
else:
    count = sum(r.units for r in returned)
    result = ReturnRate(sold_units=sold.units, returned_units=count, rate=count/sold.units if sold.units else None)
result'''
    if case == "promotion-review":
        return f'''before = {requests[0]}
after = {requests[1]}
promotions = {requests[2]}
query(before)
query(after)
query(promotions)
comparison = CategoryComparisonClaim(before=before, after=after)
pauses = PromotionPauseClaim(source=promotions, comparison=comparison)
Answer(claims={{"comparison": comparison, "pauses": pauses}},
       text="Category changes: {{comparison}}. Promotions to pause: {{pauses}}.")'''
    raise ValueError("No reference workflow")

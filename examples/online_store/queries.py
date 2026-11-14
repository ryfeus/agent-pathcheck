"""Fixed, parameterized reads. Registry identifiers never become arbitrary SQL."""

from datetime import date, timedelta
from agent_pathcheck import DataBlocked
from .concepts import (gold_customer, late_delivery, local_date, month_shift,
                       period_bounds, previous_month, utc_boundary)
from .data import EVENTS
from .models import CustomerState, RestockRow


def register_functions(connection, config):
    connection.create_function("store_day", 1, lambda value: local_date(value, config.timezone).toordinal(), deterministic=True)
    connection.create_function("is_late", 3,
                               lambda shipped, delivered, cutoff: int(late_delivery(shipped, delivered, cutoff, config)),
                               deterministic=True)


def bounds(period, config):
    start, end = period_bounds(period)
    return dict(start=utc_boundary(start.isoformat(), config.timezone),
                end=utc_boundary(end.isoformat(), config.timezone))


def customer_rows(sql, cutoff, config):
    end = utc_boundary(cutoff, config.timezone)
    start = utc_boundary(month_shift(date.fromisoformat(cutoff), -12).isoformat(), config.timezone)
    return sql(EVENTS + """,
      activity AS (SELECT customer_id, MAX(placed_at) AS last_order_at, COUNT(*) AS order_count
                   FROM valid_orders WHERE placed_at < :end GROUP BY customer_id),
      revenue AS (SELECT customer_id, SUM(revenue_cents) AS net_revenue_cents FROM events
                  WHERE event_at >= :start AND event_at < :end GROUP BY customer_id)
      SELECT c.customer_id, a.last_order_at, COALESCE(r.net_revenue_cents,0) AS net_revenue_cents,
             COALESCE(a.order_count,0) AS order_count
      FROM customers c LEFT JOIN activity a USING(customer_id) LEFT JOIN revenue r USING(customer_id)
      ORDER BY c.customer_id
    """, dict(start=start, end=end))


def measure_rows(sql, request, config):
    params = {**bounds(request.period, config), "region": request.region, "channel": request.channel}
    gold_filter = ""
    if request.customer_tier == "gold":
        cutoff = period_bounds(request.period)[1].isoformat()
        customers = [CustomerState.model_validate(row) for row in customer_rows(sql, cutoff, config)]
        ids = [row.customer_id for row in customers if gold_customer(row, cutoff, config)]
        # Even an empty eligible set executes its scoped source read. It is not
        # permission to bypass required evidence or hide a failed operation.
        names = [f"gold_{i}" for i in range(len(ids))]
        params.update(zip(names, ids))
        gold_filter = " AND customer_id IN (" + ",".join(":" + name for name in names) + ")" if ids else " AND 0"
    if request.metric == "net_revenue":
        group = {"category": "e.category_id", "customer": "e.customer_id"}[request.group_by]
        label = "c.name" if request.group_by == "category" else group
        dimension = " JOIN categories c ON c.category_id=e.category_id" if request.group_by == "category" else ""
        return sql(EVENTS + f"""
          SELECT {group} AS key, {label} AS label, SUM(revenue_cents) AS value FROM events e {dimension}
          WHERE event_at >= :start AND event_at < :end
            AND (:region='all' OR region=:region) AND (:channel='all' OR channel=:channel)
            {gold_filter} GROUP BY {group} ORDER BY {group}
        """, params)
    group = {"supplier": "p.supplier_id", "customer": "o.customer_id"}[request.group_by]
    params["cutoff"] = period_bounds(request.period)[1].isoformat()
    late_filter = " AND is_late(s.shipped_at,s.delivered_at,:cutoff)=1" if request.metric == "late_deliveries" else ""
    # An order can contribute once to each supplier, never once per line.
    return sql(EVENTS + f"""
      SELECT {group} AS key, {group} AS label, COUNT(DISTINCT o.order_id) AS value
      FROM valid_orders o JOIN shipments s USING(order_id) JOIN order_lines l USING(order_id)
      JOIN products p USING(product_id)
      WHERE s.shipped_at >= :start AND s.shipped_at < :end
        AND (:region='all' OR o.region=:region) AND (:channel='all' OR o.channel=:channel)
        {gold_filter} {late_filter} GROUP BY {group} ORDER BY {group}
    """, params)


def stock_rows(sql, config):
    cutoff = date.fromisoformat(config.cutoff)
    params = {**bounds(previous_month(config), config), "snapshot": config.cutoff,
              "history": utc_boundary((cutoff - timedelta(days=90)).isoformat(), config.timezone),
              "velocity": utc_boundary((cutoff - timedelta(days=30)).isoformat(), config.timezone),
              "cutoff": utc_boundary(config.cutoff, config.timezone)}
    rows = sql(EVENTS + """,
      stock AS (
        SELECT product_id,
          SUM(CASE WHEN date=:snapshot THEN on_hand END) AS on_hand,
          SUM(CASE WHEN date=:snapshot THEN reorder_point END) AS reorder_point,
          CASE WHEN SUM(CASE WHEN date=:snapshot THEN 1 ELSE 0 END)=COUNT(DISTINCT warehouse_id)
               THEN 1 ELSE 0 END AS inventory_complete
        FROM inventory WHERE date<=:snapshot GROUP BY product_id
      ), history AS (
        SELECT l.product_id, COUNT(DISTINCT store_day(o.placed_at)) AS history_days,
               SUM(CASE WHEN o.placed_at>=:velocity THEN l.quantity ELSE 0 END) AS units_30
        FROM valid_orders o JOIN order_lines l USING(order_id)
        WHERE o.placed_at>=:history AND o.placed_at<:cutoff GROUP BY l.product_id
      ), finance AS (
        SELECT product_id, SUM(revenue_cents) AS monthly_sales_cents,
               CASE WHEN SUM(CASE WHEN cost_cents IS NULL THEN 1 ELSE 0 END)>0 THEN NULL
                    ELSE SUM(cost_cents) END AS monthly_cost_cents
        FROM events WHERE event_at>=:start AND event_at<:end GROUP BY product_id
      )
      SELECT p.product_id, p.status AS product_status, s.status AS supplier_status, s.lead_time_days,
             i.on_hand, i.reorder_point, COALESCE(i.inventory_complete,0) AS inventory_complete,
             COALESCE(h.history_days,0) AS history_days, COALESCE(h.units_30,0) AS units_30,
             COALESCE(f.monthly_sales_cents,0) AS monthly_sales_cents,
             CASE WHEN f.product_id IS NULL THEN 0 ELSE f.monthly_cost_cents END AS monthly_cost_cents,
             fc.lost_revenue_cents
      FROM products p JOIN suppliers s USING(supplier_id)
      LEFT JOIN stock i USING(product_id) LEFT JOIN history h USING(product_id)
      LEFT JOIN finance f USING(product_id)
      LEFT JOIN forecasts fc ON fc.product_id=p.product_id AND fc.date=:snapshot
      ORDER BY p.product_id
    """, params)
    from .rules import restock_reason
    for row in rows:
        # Unknown facts about a potentially eligible candidate must block the
        # required read, so they cannot silently disappear from the ranking.
        restock_reason(RestockRow.model_validate(row), config)
    return rows


def refund_rows(sql, request):
    return sql("""
      SELECT l.line_id, o.order_id, o.customer_id, o.storefront, l.quantity,
             l.quantity*l.unit_price_cents-l.discount_cents AS paid_cents,
             COALESCE(SUM(r.refund_cents),0) AS refunded_cents,
             COALESCE(SUM(r.quantity),0) AS returned_quantity, s.delivered_at,
             CASE WHEN s.delivered_at IS NULL THEN NULL
                  ELSE store_day(:requested_at)-store_day(s.delivered_at) END AS delivery_age_days
      FROM order_lines l JOIN orders o USING(order_id) LEFT JOIN shipments s USING(order_id)
      LEFT JOIN returns r ON r.line_id=l.line_id AND r.returned_at<=:requested_at
      WHERE l.line_id=:line_id AND o.storefront='prod'
      GROUP BY l.line_id
    """, dict(line_id=request.line_id, requested_at=request.requested_at))


def margin_rows(sql, request, config):
    rows = sql(EVENTS + """
      SELECT p.product_id, COALESCE(SUM(e.revenue_cents),0) AS net_revenue_cents,
             CASE WHEN p.unit_cost_cents IS NULL THEN NULL ELSE COALESCE(SUM(e.cost_cents),0) END AS cost_cents
      FROM products p LEFT JOIN events e ON e.product_id=p.product_id AND e.event_at>=:start AND e.event_at<:end
      WHERE p.product_id=:product_id GROUP BY p.product_id
    """, {**bounds(request.period, config), "product_id": request.product_id})
    if not rows or rows[0]["cost_cents"] is None:
        raise DataBlocked("store.cost_missing", "Unit cost is missing for the requested product")
    return rows


def sold_units(sql, request, config):
    return sql(EVENTS + """
      SELECT COALESCE(SUM(sold_units),0) AS units FROM events
      WHERE event_at>=:start AND event_at<:end
    """, bounds(request.period, config))


def returned_units(sql, request, config):
    return sql(EVENTS + """
      SELECT r.return_id, r.line_id, r.quantity AS units
      FROM returns r JOIN order_lines l USING(line_id) JOIN valid_orders o USING(order_id)
      WHERE o.placed_at>=:start AND o.placed_at<:end AND r.returned_at<:cutoff
      ORDER BY r.return_id
    """, {**bounds(request.period, config), "cutoff": utc_boundary(request.cutoff, config.timezone)})


def promotion_rows(sql, request, config):
    previous = bounds(request.previous_period, config)
    rows = sql(EVENTS + """,
      declining AS (
        SELECT category_id FROM events WHERE region=:region
        GROUP BY category_id
        HAVING SUM(CASE WHEN event_at>=:start AND event_at<:end THEN revenue_cents ELSE 0 END)
             < SUM(CASE WHEN event_at>=:previous_start AND event_at<:previous_end THEN revenue_cents ELSE 0 END)
      )
      SELECT pr.promo_id, pr.category_id, pr.discount_bps,
             SUM(e.revenue_cents) AS net_revenue_cents,
             CASE WHEN SUM(CASE WHEN e.cost_cents IS NULL THEN 1 ELSE 0 END)>0 THEN NULL
                  ELSE SUM(e.revenue_cents)-SUM(e.cost_cents) END AS margin_cents,
             SUM(e.sold_units) AS sold_units
      FROM promotions pr JOIN events e ON e.category_id=pr.category_id
        AND e.event_at>=pr.starts_at AND e.event_at<pr.ends_at
      WHERE e.event_at>=:start AND e.event_at<:end AND e.region=:region
        AND pr.category_id IN (SELECT category_id FROM declining)
      GROUP BY pr.promo_id HAVING SUM(e.sold_units)>0 ORDER BY pr.promo_id
    """, {**bounds(request.period, config), "region": request.region,
           "previous_start": previous["start"], "previous_end": previous["end"]})
    for row in rows:
        if row["margin_cents"] is None or row["net_revenue_cents"] <= 0:
            raise DataBlocked("store.promotion_margin", "An observed promotion has missing cost or undefined margin rate")
    return rows

"""Deterministic, synthetic source records. Never exposed in tool discovery."""

from datetime import date, timedelta
from .concepts import utc_boundary
from .data import EVENTS, create_schema, validate_integrity


ZONE = "America/Los_Angeles"


def stamp(day):
    # Noon in the store timezone, always emitted in canonical UTC form.
    from datetime import datetime, time, timezone
    from zoneinfo import ZoneInfo
    return datetime.combine(date.fromisoformat(day), time(12), ZoneInfo(ZONE)).astimezone(timezone.utc).isoformat()


def insert(connection, table, **values):
    connection.execute(f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
                       tuple(values.values()))


def order(connection, oid, customer, product, day, amount, *, quantity=1, discount=0,
          shipped=None, delivered=None, storefront="prod", channel="web", status="paid", line_id=None):
    insert(connection, "orders", order_id=oid, customer_id=customer, placed_at=stamp(day),
           storefront=storefront, channel=channel, status=status)
    insert(connection, "order_lines", line_id=line_id or "L-" + oid, order_id=oid, product_id=product,
           quantity=quantity, unit_price_cents=amount, discount_cents=discount)
    if shipped:
        insert(connection, "shipments", order_id=oid, shipped_at=stamp(shipped),
               delivered_at=stamp(delivered) if delivered else None, carrier="synthetic")


def seed(connection):
    for category in ("Apparel", "Electronics", "Home", "Beauty", "Books", "Outdoor"):
        insert(connection, "categories", category_id=category, name=category,
               status="discontinued" if category == "Outdoor" else "active")
    for sid in ("S-1", "S-2", "S-inactive"):
        insert(connection, "suppliers", supplier_id=sid, status="inactive" if sid == "S-inactive" else "active",
               lead_time_days=14, region="West")
    for cid in ("C-balance", "C-refund", "C-new", "C-window", "C-churn", "C-gold", "C-not-gold", "C-future", "C-west"):
        insert(connection, "customers", customer_id=cid, signup_date="2025-01-01",
               region="West" if cid == "C-west" else "East", segment="consumer", marketing_opt_in=1)
    products = [
        ("P-101", "Rain jacket", "Apparel", "S-1", "active", 10000, 1000, 420000),
        ("P-102", "Trail shoes", "Apparel", "S-2", "active", 9000, 1000, 390000),
        ("P-103", "Phone case", "Electronics", "S-1", "active", 1000, 100, 300000),
        ("P-104", "Desk lamp", "Home", "S-inactive", "active", 4000, 100, 600000),
        ("P-105", "Yoga mat", "Home", "S-1", "discontinued", 2000, 100, 250000),
        ("P-106", "Water bottle", "Home", "S-1", "active", 500, 1000, 150000),
        ("P-107", "Backpack", "Apparel", "S-1", "active", 6000, 1000, 210000),
        ("P-108", "Headphones", "Electronics", "S-2", "active", 7000, 1000, 210000),
        ("P-109", "Notebook", "Books", "S-1", "active", 200, 10, 80000),
        ("P-110", "Mug", "Home", "S-1", "active", 200, 10, 65000),
        ("P-113", "Sibling phone case", "Electronics", "S-1", "active", 1, 0, 100000),
        ("P-120", "New product", "Home", "S-1", "active", 10000, None, 100000),
    ]
    for pid, name, category, supplier, status, price, cost, forecast in products:
        insert(connection, "products", product_id=pid, name=name, category_id=category,
               supplier_id=supplier, status=status, list_price_cents=price, unit_cost_cents=cost)
        insert(connection, "inventory", product_id=pid, warehouse_id="W-1", date="2026-11-01",
               on_hand=10000 if pid in ("P-113", "P-120") else 1, reorder_point=10)
        insert(connection, "forecasts", product_id=pid, date="2026-11-01", lost_revenue_cents=forecast)
        if pid == "P-120":
            continue
        days = 90 if pid == "P-113" else 20 if pid == "P-103" else 29
        start = date(2026, 8, 3) if pid == "P-113" else date(2026, 8, 12)
        for i in range(days):
            day = (start + timedelta(days=i)).isoformat()
            order(connection, f"H-{pid}-{i}", "C-balance", pid, day, 1)
        if pid != "P-113":
            order(connection, "M-" + pid, "C-balance", pid, "2026-10-20", price,
                  quantity=30, discount=2000 if pid == "P-109" else 0)
    for category in ("Apparel", "Electronics", "Home", "Beauty", "Books", "Outdoor"):
        insert(connection, "products", product_id="B-" + category, name="Accounting fixture " + category,
               category_id=category, supplier_id="S-1", status="discontinued" if category == "Outdoor" else "active",
               list_price_cents=1, unit_cost_cents=0)
        insert(connection, "inventory", product_id="B-" + category, warehouse_id="W-1", date="2026-11-01",
               on_hand=10000, reorder_point=1)
    for pid, category, cost in (("W-app", "Apparel", 900000), ("W-elect", "Electronics", 400000), ("W-home", "Home", 180000)):
        insert(connection, "products", product_id=pid, name=pid, category_id=category, supplier_id="S-2",
               status="active", list_price_cents=1000000, unit_cost_cents=cost)
        insert(connection, "inventory", product_id=pid, warehouse_id="W-1", date="2026-11-01", on_hand=1000, reorder_point=1)
    order(connection, "west-app-sep", "C-west", "W-app", "2026-09-15", 1250000, quantity=2, discount=500000)
    order(connection, "west-app-oct", "C-west", "W-app", "2026-10-15", 1250000, discount=250000)
    for pid, amounts in (("W-elect", (1000000, 800000)), ("W-home", (100000, 200000))):
        for month, amount in zip(("09", "10"), amounts):
            order(connection, f"west-{pid}-{month}", "C-west", pid, f"2026-{month}-15", amount)
    for promo, category, bps in (("PROMO-app", "Apparel", 2000), ("PROMO-elect", "Electronics", 3000), ("PROMO-home", "Home", 3000)):
        insert(connection, "promotions", promo_id=promo, category_id=category, discount_bps=bps,
               starts_at=utc_boundary("2026-10-01", ZONE), ends_at=utc_boundary("2026-11-01", ZONE))
    order(connection, "1042", "C-refund", "B-Home", "2026-09-30", 100000, quantity=2,
          shipped="2026-10-01", delivered="2026-10-01", line_id="L-1042")
    order(connection, "old-sale", "C-window", "B-Apparel", "2025-09-15", 20000,
          shipped="2025-09-16", delivered="2025-09-20")
    insert(connection, "returns", return_id="R-old", line_id="L-old-sale", customer_id="C-window",
           returned_at=stamp("2025-10-15"), quantity=1, refund_cents=20000, reason="synthetic")
    for oid, customer, day, amount in (
        ("window", "C-window", "2026-08-17", 210000), ("churn", "C-churn", "2026-07-25", 10000),
        ("new-sep", "C-new", "2026-09-15", 100000), ("new-oct", "C-new", "2026-10-15", 120000),
        ("gold", "C-gold", "2026-07-01", 300000), ("future-gold", "C-future", "2026-10-02", 300000),
    ):
        order(connection, oid, customer, "B-Apparel", day, amount)
    order(connection, "q3-late", "C-gold", "P-101", "2026-09-10", 100,
          shipped="2026-09-12", delivered="2026-09-20")
    for lid, product in (("q3-extra-one", "P-107"), ("q3-extra-two", "P-102")):
        insert(connection, "order_lines", line_id=lid, order_id="q3-late", product_id=product,
               quantity=1, unit_price_cents=100, discount_cents=0)
    order(connection, "q3-overdue", "C-gold", "P-101", "2026-09-24", 100, shipped="2026-09-25")
    order(connection, "q3-not-yet-late", "C-gold", "P-102", "2026-09-26", 100,
          shipped="2026-09-27", delivered="2026-10-15")
    order(connection, "q3-not-gold", "C-not-gold", "P-102", "2026-09-01", 100,
          shipped="2026-09-02", delivered="2026-09-20")
    order(connection, "weekly", "C-new", "P-102", "2026-10-21", 100, shipped="2026-10-25", delivered="2026-10-26")
    order(connection, "new-product", "C-new", "P-120", "2026-10-20", 10000, shipped="2026-10-22", delivered="2026-10-23")
    order(connection, "return-cohort", "C-refund", "B-Books", "2026-10-02", 100, quantity=200,
          shipped="2026-10-03", delivered="2026-10-04")
    insert(connection, "returns", return_id="R-oct", line_id="L-return-cohort", customer_id="C-refund",
           returned_at=stamp("2026-10-10"), quantity=2, refund_cents=200, reason="synthetic")
    insert(connection, "returns", return_id="R-prior-order", line_id="L-1042", customer_id="C-refund",
           returned_at=stamp("2026-10-15"), quantity=1, refund_cents=10000, reason="synthetic")
    # Decoys make status and storefront mistakes observable.
    for suffix, storefront, status in (("test", "test", "paid"), ("pending", "prod", "pending"), ("cancelled", "prod", "cancelled")):
        order(connection, "decoy-" + suffix, "C-west", "B-Apparel", "2026-10-15", 99999999,
              storefront=storefront, status=status)
    # Balance source transactions, not precomputed query results. Every later read
    # still derives the totals from order lines and independently dated refunds.
    targets = {"2026-09": {"Apparel":12000000,"Electronics":9000000,"Home":6000000,"Books":3000000,"Outdoor":300000},
               "2026-10": {"Apparel":14500000,"Electronics":10800000,"Home":6800000,"Beauty":600000,"Books":3500000}}
    for month, categories in targets.items():
        start = utc_boundary(month + "-01", ZONE)
        end = utc_boundary("2026-10-01" if month == "2026-09" else "2026-11-01", ZONE)
        for category, target in categories.items():
            actual = connection.execute(EVENTS + "SELECT COALESCE(SUM(revenue_cents),0) FROM events WHERE category_id=? AND event_at>=? AND event_at<?",
                                        (category, start, end)).fetchone()[0]
            assert target > actual
            order(connection, f"balance-{month}-{category}", "C-balance", "B-" + category, month + "-28", target - actual)


def initialize(connection, *, fixture="base", extra_sql=""):
    create_schema(connection)
    seed(connection)
    if fixture == "truncated-returns":
        for i in range(101):
            insert(connection, "returns", return_id=f"R-limit-{i}", line_id="L-return-cohort", customer_id="C-refund",
                   returned_at=stamp("2026-10-20"), quantity=1, refund_cents=1, reason="row-limit fixture")
    elif fixture != "base":
        raise ValueError("Unknown fixture")
    if extra_sql:
        connection.executescript(extra_sql)
    validate_integrity(connection)

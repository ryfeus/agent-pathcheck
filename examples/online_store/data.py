"""SQLite source schema and integrity checks, independent of expected answers."""

import sqlite3
from pydantic import ValidationError
from . import models


TABLE_MODELS = {
    "customers": models.Customer, "orders": models.Order, "order_lines": models.OrderLine,
    "products": models.Product, "categories": models.Category, "shipments": models.Shipment,
    "returns": models.Return, "inventory": models.InventorySnapshot, "suppliers": models.Supplier,
    "promotions": models.Promotion, "forecasts": models.Forecast,
}

SCHEMA = """
CREATE TABLE customers(customer_id TEXT PRIMARY KEY, signup_date TEXT NOT NULL, region TEXT NOT NULL,
 segment TEXT NOT NULL, marketing_opt_in INTEGER NOT NULL);
CREATE TABLE categories(category_id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, status TEXT NOT NULL);
CREATE TABLE suppliers(supplier_id TEXT PRIMARY KEY, status TEXT NOT NULL, lead_time_days INTEGER NOT NULL, region TEXT NOT NULL);
CREATE TABLE products(product_id TEXT PRIMARY KEY, name TEXT NOT NULL,
 category_id TEXT NOT NULL REFERENCES categories, supplier_id TEXT NOT NULL REFERENCES suppliers,
 status TEXT NOT NULL, list_price_cents INTEGER NOT NULL, unit_cost_cents INTEGER);
CREATE TABLE orders(order_id TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers,
 placed_at TEXT NOT NULL, storefront TEXT NOT NULL, channel TEXT NOT NULL, status TEXT NOT NULL);
CREATE TABLE order_lines(line_id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders,
 product_id TEXT NOT NULL REFERENCES products, quantity INTEGER NOT NULL,
 unit_price_cents INTEGER NOT NULL, discount_cents INTEGER NOT NULL,
 CHECK(discount_cents <= quantity * unit_price_cents));
CREATE TABLE shipments(order_id TEXT PRIMARY KEY REFERENCES orders, shipped_at TEXT NOT NULL,
 delivered_at TEXT, carrier TEXT NOT NULL, CHECK(delivered_at IS NULL OR delivered_at >= shipped_at));
CREATE TABLE returns(return_id TEXT PRIMARY KEY, line_id TEXT NOT NULL REFERENCES order_lines,
 customer_id TEXT NOT NULL REFERENCES customers, returned_at TEXT NOT NULL,
 quantity INTEGER NOT NULL, refund_cents INTEGER NOT NULL, reason TEXT NOT NULL);
CREATE TABLE inventory(product_id TEXT NOT NULL REFERENCES products, warehouse_id TEXT NOT NULL,
 date TEXT NOT NULL, on_hand INTEGER NOT NULL, reorder_point INTEGER NOT NULL,
 PRIMARY KEY(product_id, warehouse_id, date));
CREATE TABLE promotions(promo_id TEXT PRIMARY KEY, category_id TEXT NOT NULL REFERENCES categories,
 discount_bps INTEGER NOT NULL, starts_at TEXT NOT NULL, ends_at TEXT NOT NULL, CHECK(ends_at > starts_at));
CREATE TABLE forecasts(product_id TEXT NOT NULL REFERENCES products, date TEXT NOT NULL,
 lost_revenue_cents INTEGER NOT NULL, PRIMARY KEY(product_id, date));
CREATE INDEX orders_placed ON orders(placed_at);
CREATE INDEX lines_order ON order_lines(order_id);
CREATE INDEX returns_line ON returns(line_id);
"""


def create_schema(connection):
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA)


def validate_integrity(connection):
    """Validate before exposing reads; a corrupt fixture is not an empty result."""
    if connection.execute("PRAGMA foreign_key_check").fetchone():
        raise sqlite3.IntegrityError("Broken store relationship")
    try:
        for table, model in TABLE_MODELS.items():
            cursor = connection.execute(f"SELECT * FROM {table}")
            fields = [item[0] for item in cursor.description]
            for row in cursor:
                model.model_validate(dict(zip(fields, row)))
    except ValidationError as exc:
        raise sqlite3.IntegrityError(str(exc)) from None
    invalid = connection.execute("""
        SELECT r.return_id FROM returns r JOIN order_lines l USING(line_id)
        JOIN orders o USING(order_id) LEFT JOIN shipments s USING(order_id)
        WHERE r.customer_id != o.customer_id OR s.delivered_at IS NULL OR r.returned_at < s.delivered_at
        UNION ALL
        SELECT l.line_id FROM order_lines l JOIN returns r USING(line_id)
        GROUP BY l.line_id HAVING SUM(r.refund_cents) > l.quantity*l.unit_price_cents-l.discount_cents
                              OR SUM(r.quantity) > l.quantity
        UNION ALL
        SELECT s.order_id FROM shipments s JOIN orders o USING(order_id) WHERE s.shipped_at < o.placed_at
    """).fetchone()
    if invalid:
        raise sqlite3.IntegrityError("Invalid return ownership, chronology, quantity, or amount: " + str(invalid[0]))


# All measures inherit these production/status predicates. Returns are events
# on their own dates, and never join directly into the sale aggregation.
EVENTS = """
WITH valid_orders AS (
 SELECT o.*, c.region FROM orders o JOIN customers c USING(customer_id)
 WHERE o.storefront='prod' AND o.status IN ('paid','shipped','delivered','refunded')
), events AS (
 SELECT o.order_id, o.customer_id, o.channel, o.region, l.product_id,
        p.category_id, p.supplier_id, o.placed_at AS event_at,
        l.quantity*l.unit_price_cents-l.discount_cents AS revenue_cents,
        l.quantity AS sold_units, l.quantity*p.unit_cost_cents AS cost_cents
 FROM valid_orders o JOIN order_lines l USING(order_id) JOIN products p USING(product_id)
 UNION ALL
 SELECT o.order_id, o.customer_id, o.channel, o.region, l.product_id,
        p.category_id, p.supplier_id, r.returned_at, -r.refund_cents, 0, 0
 FROM returns r JOIN order_lines l USING(line_id) JOIN valid_orders o USING(order_id)
 JOIN products p USING(product_id)
)
"""

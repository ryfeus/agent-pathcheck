"""Strict store entities and operation result rows; no fixture answers."""

from typing import Literal
from pydantic import Field, field_validator
from datetime import datetime, date
from agent_pathcheck import PolicyModel
from .concepts import active_customer


class Timestamped(PolicyModel):
    @field_validator("*", mode="after")
    @classmethod
    def valid_dates(cls, value, info):
        if value is None:
            return value
        if info.field_name.endswith("_at"):
            parsed = datetime.fromisoformat(value)
            if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
                raise ValueError("Timestamps must use UTC with an explicit offset")
            if value != parsed.isoformat(timespec="seconds"):
                raise ValueError("Timestamps must use canonical ISO seconds and +00:00")
        if info.field_name in ("date", "signup_date"):
            date.fromisoformat(value)
        return value


class Customer(Timestamped):
    customer_id: str
    signup_date: str
    region: str
    segment: Literal["consumer", "business"]
    marketing_opt_in: Literal[0, 1]

    is_active = staticmethod(active_customer)


class Order(Timestamped):
    order_id: str
    customer_id: str
    placed_at: str
    storefront: Literal["prod", "test"]
    channel: Literal["web", "app", "marketplace"]
    status: Literal["pending", "cancelled", "paid", "shipped", "delivered", "refunded"]


class OrderLine(PolicyModel):
    line_id: str
    order_id: str
    product_id: str
    quantity: int = Field(gt=0)
    unit_price_cents: int = Field(ge=0)
    discount_cents: int = Field(ge=0)


class Product(PolicyModel):
    product_id: str
    name: str
    category_id: str
    supplier_id: str
    status: Literal["active", "discontinued"]
    list_price_cents: int = Field(ge=0)
    unit_cost_cents: int | None = Field(default=None, ge=0)


class Category(PolicyModel):
    category_id: str
    name: str
    status: Literal["active", "discontinued"]


class Shipment(Timestamped):
    order_id: str
    shipped_at: str
    delivered_at: str | None
    carrier: str


class Return(Timestamped):
    return_id: str
    line_id: str
    customer_id: str
    returned_at: str
    quantity: int = Field(gt=0)
    refund_cents: int = Field(ge=0)
    reason: str


class InventorySnapshot(Timestamped):
    product_id: str
    warehouse_id: str
    date: str
    on_hand: int = Field(ge=0)
    reorder_point: int = Field(ge=0)


class Supplier(PolicyModel):
    supplier_id: str
    status: Literal["active", "inactive"]
    lead_time_days: int = Field(ge=0)
    region: str


class Promotion(Timestamped):
    promo_id: str
    category_id: str
    discount_bps: int = Field(ge=0, le=10000)
    starts_at: str
    ends_at: str


class Forecast(Timestamped):
    product_id: str
    date: str
    lost_revenue_cents: int = Field(ge=0)


class MeasureRow(PolicyModel):
    key: str
    label: str
    value: int
    before: int | None = None
    after: int | None = None


class CustomerState(Timestamped):
    customer_id: str
    last_order_at: str | None
    net_revenue_cents: int
    order_count: int = Field(ge=0)


class RestockRow(PolicyModel):
    product_id: str
    product_status: Literal["active", "discontinued"]
    supplier_status: Literal["active", "inactive"]
    lead_time_days: int = Field(ge=0)
    on_hand: int | None = Field(ge=0)
    reorder_point: int | None = Field(ge=0)
    inventory_complete: Literal[0, 1]
    history_days: int = Field(ge=0, le=90)
    units_30: int = Field(ge=0)
    monthly_sales_cents: int
    monthly_cost_cents: int | None
    lost_revenue_cents: int | None = Field(ge=0)


class RefundEvidence(Timestamped):
    line_id: str
    order_id: str
    customer_id: str
    storefront: Literal["prod"]
    quantity: int = Field(gt=0)
    paid_cents: int = Field(ge=0)
    refunded_cents: int = Field(ge=0)
    returned_quantity: int = Field(ge=0)
    delivered_at: str | None
    delivery_age_days: int | None


class RefundRequest(Timestamped):
    order_id: str = "1042"
    line_id: str = "L-1042"
    customer_id: str = "C-refund"
    requested_at: str = "2026-10-31T19:00:00+00:00"
    quantity: int = Field(default=1, gt=0)
    refund_cents: int = Field(default=50000, ge=0)


class UnitsRow(PolicyModel):
    units: int = Field(ge=0)


class ReturnedUnits(PolicyModel):
    return_id: str
    line_id: str
    units: int = Field(gt=0)


class MarginRow(PolicyModel):
    product_id: str
    net_revenue_cents: int
    cost_cents: int


class PromotionRow(PolicyModel):
    promo_id: str
    category_id: str
    discount_bps: int = Field(ge=0, le=10000)
    net_revenue_cents: int
    margin_cents: int
    sold_units: int = Field(gt=0)

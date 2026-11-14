"""Shared business definitions. These functions run on the trusted host."""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator
from agent_pathcheck import PolicyModel


class StoreConfig(PolicyModel):
    timezone: str = "America/Los_Angeles"
    cutoff: str = "2026-11-01"
    active_days: int = Field(default=90, ge=1, le=365)
    gold_cents: int = Field(default=200000, ge=0)
    refund_days: int = Field(default=30, ge=0)
    auto_refund_cents: int = Field(default=50000, ge=0)
    history_days: int = Field(default=30, ge=1, le=90)
    minimum_monthly_sales_cents: int = Field(default=0, ge=0)
    restock_size: int = Field(default=5, ge=1, le=20)
    sla_days: int = Field(default=5, ge=0)

    @model_validator(mode="after")
    def valid_reporting_snapshot(self):
        ZoneInfo(self.timezone)
        if date.fromisoformat(self.cutoff).day != 1:
            raise ValueError("The example's monthly reporting cutoff must be a month start")
        return self


QUALIFYING_STATUSES = ("paid", "shipped", "delivered", "refunded")


def local_date(timestamp, zone):
    return datetime.fromisoformat(timestamp).astimezone(ZoneInfo(zone)).date()


def utc_boundary(day, zone):
    return datetime.combine(date.fromisoformat(day), time(), ZoneInfo(zone)).astimezone(
        timezone.utc).isoformat(timespec="seconds")


def month_shift(day, months):
    year, month = divmod(day.year * 12 + day.month - 1 + months, 12)
    return date(year, month + 1, 1)


def period_bounds(period):
    if "/" in period:
        start, end = period.split("/")
        return date.fromisoformat(start), date.fromisoformat(end)
    if "-Q" in period:
        year, quarter = period.split("-Q")
        start = date(int(year), (int(quarter) - 1) * 3 + 1, 1)
        return start, month_shift(start, 3)
    start = date.fromisoformat(period + "-01")
    return start, month_shift(start, 1)


def previous_month(config):
    return month_shift(date.fromisoformat(config.cutoff), -1).strftime("%Y-%m")


def previous_week(config):
    cutoff = date.fromisoformat(config.cutoff)
    end = cutoff - timedelta(days=cutoff.weekday())
    return (end - timedelta(days=7)).isoformat(), end.isoformat()


def active_customer(last_order_at, cutoff, config):
    if last_order_at is None:
        return False
    age = (date.fromisoformat(cutoff) - local_date(last_order_at, config.timezone)).days
    return 0 < age <= config.active_days


def gold_customer(row, cutoff, config):
    return (active_customer(row.last_order_at, cutoff, config)
            and row.net_revenue_cents >= config.gold_cents)


def late_delivery(shipped_at, delivered_at, cutoff, config):
    end = utc_boundary(cutoff, config.timezone)
    observed = delivered_at if delivered_at is not None and delivered_at < end else end
    return (local_date(observed, config.timezone) - local_date(shipped_at, config.timezone)).days > config.sla_days


CONCEPT_DEPENDENCIES = {
    "active_customer": ("loyalty", "churn", "composed:customer_tier=gold"),
    "gold_customer": ("loyalty", "composed:customer_tier=gold"),
    "net_revenue": ("revenue", "category-comparison", "restock", "loyalty", "product-margin",
                    "promotion-review", "composed:net_revenue", "composed:customer_tier=gold"),
    "store_timezone": ("all",),
}


def affected_policies(concept):
    return list(CONCEPT_DEPENDENCIES[concept])

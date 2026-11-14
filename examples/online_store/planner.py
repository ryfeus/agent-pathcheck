"""A bounded example-local compiler and optional natural-language front end."""

import json
from typing import Literal
from pydantic import Field, model_validator
from agent_pathcheck import PolicyModel
from .concepts import StoreConfig, period_bounds


REGISTRY = {
    "net_revenue": {"groups": ["category", "customer"], "unit": "cents",
                    "relationships": ["OrderLine→Order→Customer", "Return→OrderLine", "OrderLine→Product→Category"],
                    "rules": ["store.scope", "store.metric_result"]},
    "shipped_orders": {"groups": ["supplier", "customer"], "unit": "orders",
                       "relationships": ["Shipment→Order→Customer", "Order→OrderLine→Product→Supplier"],
                       "rules": ["store.scope", "store.metric_result"]},
    "late_deliveries": {"groups": ["supplier", "customer"], "unit": "orders",
                        "relationships": ["Shipment→Order→Customer", "Order→OrderLine→Product→Supplier"],
                        "rules": ["store.scope", "store.metric_result", "store.late_delivery"]},
}


class PlanSpec(PolicyModel):
    measure: Literal["net_revenue", "shipped_orders", "late_deliveries"]
    group_by: Literal["category", "customer", "supplier"]
    periods: list[str] = Field(min_length=1, max_length=2)
    shape: Literal["aggregate", "comparison", "top_k"] = "aggregate"
    top: int | None = Field(default=None, ge=1, le=100)
    region: Literal["all", "West", "East", "North", "South"] = "all"
    channel: Literal["all", "web", "app", "marketplace"] = "all"
    customer_tier: Literal["all", "gold"] = "all"

    @model_validator(mode="after")
    def supported_composition(self):
        import re
        if self.group_by not in REGISTRY[self.measure]["groups"]:
            raise ValueError("Unsupported measure/grouping combination")
        if any(not re.fullmatch(r"20\d\d-(?:0[1-9]|1[0-2]|Q[1-4])", period) for period in self.periods):
            raise ValueError("Use calendar months YYYY-MM or quarters YYYY-Qn")
        intervals = [period_bounds(period) for period in self.periods]
        if self.shape == "comparison":
            if len(intervals) != 2 or intervals[0][1] > intervals[1][0]:
                raise ValueError("Comparison requires two ordered nonoverlapping periods")
        elif len(intervals) != 1:
            raise ValueError("Aggregate and top-k require one period")
        if (self.top is not None) != (self.shape == "top_k"):
            raise ValueError("Only top-k requires a top count")
        return self


DEFAULT_PLAN = dict(measure="late_deliveries", group_by="supplier", periods=["2026-Q3"],
                    shape="top_k", top=5, customer_tier="gold")


def validate_plan(plan, config):
    from datetime import date
    spec = PlanSpec.model_validate(plan)
    if any(period_bounds(period)[1] > date.fromisoformat(config.cutoff) for period in spec.periods):
        raise ValueError("Plans cannot read beyond the host reporting cutoff")
    return spec


def describe_plan(spec, config):
    selection = f"Top {spec.top}" if spec.shape == "top_k" else spec.shape.capitalize()
    filters = ["production storefront only", f"store time {config.timezone}"]
    if spec.customer_tier == "gold":
        filters.append("Gold customers evaluated at each reporting period end")
    if spec.region != "all":
        filters.append(f"region {spec.region}")
    if spec.channel != "all":
        filters.append(f"channel {spec.channel}")
    if spec.measure == "late_deliveries":
        filters.append(f"more than {config.sla_days} local days after shipping, including overdue undelivered orders")
    return (f"{selection} {spec.group_by} by {spec.measure.replace('_', ' ')}; "
            + " then ".join(spec.periods) + "; " + "; ".join(filters) + ".")


def dependencies(spec):
    entry = REGISTRY[spec.measure]
    return {"relationships": entry["relationships"],
            "rules": [*entry["rules"], "store.composition",
                      *(["store.gold", "store.active_customer"] if spec.customer_tier == "gold" else [])]}


def compile_plan(plan, *, config=None):
    from .policy import build_policy
    config = config or StoreConfig()
    return build_policy("composed", config=config, plan=validate_plan(plan, config))


async def plan_question(api, question, *, model, config=None, adapter=None):
    """Three planning attempts, no data reads and no policy/evidence mutation."""
    from agent_pathcheck.integrations.providers import OpenAIResponses, ProviderError
    from examples.agent_support import require_model
    model = require_model(model)
    config = config or StoreConfig()
    adapter = adapter or OpenAIResponses()
    instructions = ("Translate the question to a registered online-store PlanSpec. Call submit_plan with JSON. "
                    "Use calendar periods ending no later than the cutoff. You cannot change inherited rules. "
                    "Every registered measure ALREADY includes production storefront only. Production is a storefront, "
                    "not a channel, and is not a PlanSpec field. A question asking for production data is supported "
                    "without adding a production filter. Requests for test-storefront data are unsupported. "
                    "Resolve relative periods against the supplied reporting cutoff; last month and last quarter "
                    "mean the most recent completed calendar month and quarter. "
                    "Unsupported questions must be reported as unsupported; never substitute another question.\n"
                    + json.dumps({"registry": REGISTRY, "schema": PlanSpec.model_json_schema(),
                                  "inherited_scope": {"storefront":"prod", "timezone":config.timezone},
                                  "host_configuration": config.model_dump()}, sort_keys=True))
    tools = [{"type": "function", "name": "submit_plan", "description": "Validate a proposed policy plan.",
              "parameters": {"type": "object", "properties": {"plan_json": {"type": "string"}},
                             "required": ["plan_json"], "additionalProperties": False}, "strict": True}]
    history = [{"role": "user", "content": question}]
    attempts, usage = [], []
    for _ in range(3):
        try:
            turn = await adapter.request(api, model=model, instructions=instructions, history=history, tools=tools)
        except ProviderError as exc:
            return dict(status="planning_failed", failure="api_error", **exc.metadata,
                        attempts=[*attempts,{"accepted":False,"failure":"api_error"}], model_usage=usage)
        usage.append(turn.usage)
        if not turn.complete or len(turn.calls) != 1:
            return dict(status="planning_failed", failure="unsupported_or_incomplete",
                        attempts=[*attempts,{"accepted":False,"failure":"unsupported_or_incomplete"}], model_usage=usage,
                        model_text=turn.text, model_text_status="unverified",
                        response_complete=turn.complete, response_call_count=len(turn.calls))
        call = turn.calls[0]
        try:
            args = json.loads(call.arguments) if isinstance(call.arguments, str) else call.arguments
            if (call.name != "submit_plan" or not isinstance(args, dict) or set(args) != {"plan_json"}
                    or not isinstance(args["plan_json"], str) or len(args["plan_json"]) > 10000):
                raise ValueError("Expected only a bounded plan_json string")
            spec = validate_plan(json.loads(args["plan_json"]), config)
        except (ValueError, TypeError) as exc:
            output = {"error": str(exc)}
            attempts.append(output)
            adapter.results(history, [(call, output)])
            continue
        return dict(status="planned", plan=spec.model_dump(), summary=describe_plan(spec, config),
                    interpretation_status="unverified", attempts=[*attempts, {"accepted": True}], model_usage=usage)
    return dict(status="planning_failed", failure="attempt_limit", attempts=attempts, model_usage=usage)

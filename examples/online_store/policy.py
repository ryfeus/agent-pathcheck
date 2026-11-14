"""Store policies compiled to the existing evidence/runtime interfaces."""

from contextlib import ExitStack, contextmanager
from datetime import date, timedelta
from fractions import Fraction
import json
import sqlite3
from typing import Literal

from pydantic import Field, create_model, model_validator
from agent_pathcheck import (DataBlocked, Invocation, Operation, Policy, PolicyModel, PolicyViolation,
                             bind_operations, current_evidence, current_policy, validate_model_output)
from agent_pathcheck.adapters.sqlite import open_database
from agent_pathcheck.extensions.abstain import Abstain
from agent_pathcheck.extensions.claims import AnswerEnvelope, Claim, RequiredClaim, answer_model, settle
from agent_pathcheck.rendering import Input, render_contract
from .concepts import (StoreConfig, active_customer, gold_customer, local_date, month_shift, previous_month, previous_week, utc_boundary)
from .models import (CustomerState, MarginRow, MeasureRow, PromotionRow, RefundEvidence, RefundRequest,
                     RestockRow, ReturnedUnits, UnitsRow)
from .planner import DEFAULT_PLAN, dependencies, describe_plan, validate_plan
from . import queries
from .rules import refund_decision, restock_reason, rules_for


CASES = ("revenue", "shipments", "category-comparison", "refund", "restock", "loyalty", "churn",
         "product-margin", "return-rate", "promotion-review", "composed")


def required_requests(parameters):
    return [parameters["models"][name](**scope) for name, scope in parameters["required_specs"]]


def observed(name, **selection):
    params = current_policy().parameters
    matches = [scope for model, scope in params["required_specs"] if model == name
               and all(scope.get(key) == value for key, value in selection.items())]
    if len(matches) != 1:
        raise PolicyViolation("scope", "store.scope", "Select an exact task-required read")
    request = params["models"][name](**matches[0])
    return current_evidence().require(request)


def compare_rows(before, after):
    left, right = {r.key: r for r in before}, {r.key: r for r in after}
    if len(left) != len(before) or len(right) != len(after):
        raise PolicyViolation("cardinality", "store.metric_result", "Duplicate groups cannot support a comparison")
    rows = [MeasureRow(key=key, label=(right.get(key) or left[key]).label,
                       before=left[key].value if key in left else 0,
                       after=right[key].value if key in right else 0,
                       value=(right[key].value if key in right else 0) - (left[key].value if key in left else 0))
            for key in left.keys() | right.keys()]
    return sorted(rows, key=lambda row: (-row.value, row.label, row.key))


def measure_result():
    spec = current_policy().parameters["measure_spec"]
    periods = spec["periods"]
    if spec["shape"] == "comparison":
        return compare_rows(observed("MeasureQuery", period=periods[0]), observed("MeasureQuery", period=periods[1]))
    rows = observed("MeasureQuery", period=periods[0])
    if len({row.key for row in rows}) != len(rows):
        raise PolicyViolation("cardinality", "store.metric_result", "Duplicate groups are not a complete measure")
    if spec["shape"] == "top_k":
        return sorted(rows, key=lambda row: (-row.value, row.key))[:spec["top"]]
    return sorted(rows, key=lambda row: (row.label, row.key))


class MetricResult(PolicyModel):
    rows: list[MeasureRow]
    total: int

    @model_validator(mode="after")
    def matches_evidence(self):
        expected = measure_result()
        if self.rows != expected or self.total != sum(row.value for row in expected):
            raise PolicyViolation("arithmetic", "store.metric_result", "Return every required group in order and its exact total")
        return self


def eligible_stock():
    config = current_policy().parameters["config"]
    rows = observed("RestockQuery")
    return sorted([row for row in rows if restock_reason(row, config) is None],
                  key=lambda row: (-row.lost_revenue_cents, row.product_id))[:config.restock_size]


class RestockProposal(PolicyModel):
    product_id: str

    @model_validator(mode="after")
    def eligible(self):
        by_id = {row.product_id: row for row in observed("RestockQuery")}
        row = by_id.get(self.product_id)
        reason = "undiscovered" if row is None else restock_reason(row, current_policy().parameters["config"])
        if reason:
            raise PolicyViolation("decision_rule", "store.restock_eligible", "Restock candidate failed: " + reason,
                                  entity=self.product_id)
        return self


class RestockPlan(PolicyModel):
    proposals: list[RestockProposal]
    total_lost_revenue_cents: int
    outcome: Literal["recommendations", "no_eligible_products"]

    @model_validator(mode="after")
    def complete_ranking(self):
        expected = eligible_stock()
        if ([row.product_id for row in self.proposals] != [row.product_id for row in expected]
                or self.total_lost_revenue_cents != sum(row.lost_revenue_cents for row in expected)
                or self.outcome != ("recommendations" if expected else "no_eligible_products")):
            raise PolicyViolation("completeness", "store.restock_plan", "Return the exact top eligible products, order and forecast total")
        return self


class RefundDecision(PolicyModel):
    decision: Literal["auto_approve", "human_review", "rejected"]
    reason: str

    @model_validator(mode="after")
    def matches_request(self):
        params = current_policy().parameters
        expected = refund_decision(observed("RefundQuery"), params["refund"], params["config"])
        if (self.decision, self.reason) != expected:
            raise PolicyViolation("relationship", "store.refund", "Refund decision must use this request's customer, line and delivery")
        return self


def customer_selection():
    params = current_policy().parameters
    config = params["config"]
    after = observed("CustomerQuery", cutoff=config.cutoff)
    if params["case"] == "loyalty":
        before_cutoff = month_shift(date.fromisoformat(config.cutoff), -1).isoformat()
        before = observed("CustomerQuery", cutoff=before_cutoff)
        previous = {row.customer_id for row in before if gold_customer(row, before_cutoff, config)}
        return sorted(row.customer_id for row in after if gold_customer(row, config.cutoff, config) and row.customer_id not in previous)
    selected = [row for row in after if row.last_order_at is not None and config.active_days <=
                (date.fromisoformat(config.cutoff) - local_date(row.last_order_at, config.timezone)).days < config.active_days + 90]
    return [row.customer_id for row in sorted(selected, key=lambda row: (row.last_order_at, row.customer_id))]


class CustomerList(PolicyModel):
    customer_ids: list[str]

    @model_validator(mode="after")
    def matches_shared_concepts(self):
        if self.customer_ids != customer_selection():
            raise PolicyViolation("completeness", "store.customers", "Customer list must match the shared snapshot definitions")
        return self


class ProductMargin(PolicyModel):
    margin_cents: int

    @model_validator(mode="after")
    def exact_margin(self):
        row = observed("ProductMarginQuery")[0]
        if self.margin_cents != row.net_revenue_cents - row.cost_cents:
            raise PolicyViolation("arithmetic", "store.margin", "Margin must use the exact product's observed cost")
        return self


class ReturnRate(PolicyModel):
    sold_units: int = Field(ge=0)
    returned_units: int = Field(ge=0)
    rate: float | None

    @model_validator(mode="after")
    def same_cohort(self):
        sold = observed("SalesUnitsQuery")[0].units
        returned = sum(row.units for row in observed("ReturnsQuery"))
        if ((self.sold_units, self.returned_units) != (sold, returned)
                or self.rate != (returned / sold if sold else None)):
            raise PolicyViolation("arithmetic", "store.return_rate", "Use the complete matching order cohort and cutoff")
        return self


class PauseRow(PolicyModel):
    promo_id: str
    category_id: str
    discount_bps: int
    margin_cents: int
    net_revenue_cents: int


def pause_rows(promotions, changes):
    declined = {row.key for row in changes if row.value < 0}
    selected = [row for row in promotions if row.category_id in declined
                and row.discount_bps * row.net_revenue_cents > row.margin_cents * 10000]
    selected.sort(key=lambda row: (-(Fraction(row.discount_bps, 10000) - Fraction(row.margin_cents, row.net_revenue_cents)), row.promo_id))
    return [PauseRow(**{name: getattr(row, name) for name in PauseRow.model_fields}) for row in selected]


def claim_input(value):
    value = value.model_dump() if isinstance(value, PolicyModel) else value
    if isinstance(value, dict) and isinstance(value.get("rows"), list):
        return {**value, "rows": [row.model_dump() if isinstance(row, (MeasureRow, PauseRow)) else row
                                  for row in value["rows"]]}
    return value


def promotion_claims(MeasureQuery, PromotionQuery):
    class CategoryComparisonClaim(Claim):
        before: MeasureQuery
        after: MeasureQuery
        rows: list[MeasureRow] | None = None

        @model_validator(mode="before")
        @classmethod
        def checked_rows(cls, value):
            value = claim_input(value)
            if not isinstance(value, dict) or not {"before", "after"} <= value.keys():
                return value
            before = MeasureQuery.model_validate(value["before"])
            after = MeasureQuery.model_validate(value["after"])
            rows = compare_rows(current_evidence().require(before), current_evidence().require(after))
            return settle(value, [row.model_dump() for row in rows], "store.metric_result", field="rows")

    class PromotionPauseClaim(Claim):
        source: PromotionQuery
        comparison: CategoryComparisonClaim
        rows: list[PauseRow] | None = None

        @model_validator(mode="before")
        @classmethod
        def checked_join(cls, value):
            value = claim_input(value)
            if not isinstance(value, dict) or not {"source", "comparison"} <= value.keys():
                return value
            source = PromotionQuery.model_validate(value["source"])
            comparison = CategoryComparisonClaim.model_validate(value["comparison"])
            rows = pause_rows(current_evidence().require(source), comparison.rows)
            return settle(value, [row.model_dump() for row in rows], "store.pause_join", field="rows")

    return CategoryComparisonClaim, PromotionPauseClaim


def build_policy(case="restock", policy="base", *, config=None, plan=None, refund=None):
    if case not in CASES or policy not in ("base", "minimum-monthly-sales"):
        raise ValueError("Unknown store case or policy")
    if policy != "base" and case != "restock":
        raise ValueError("The monthly-sales correction applies only to restock")
    config = StoreConfig.model_validate((config or StoreConfig()).model_dump())
    if policy == "minimum-monthly-sales":
        config = StoreConfig(**{**config.model_dump(), "minimum_monthly_sales_cents": 5000})
    refund = RefundRequest.model_validate((refund or RefundRequest()).model_dump())
    month = previous_month(config)
    previous = month_shift(date.fromisoformat(month + "-01"), -1).strftime("%Y-%m")
    models, definitions, required = {}, [], []
    common = dict(storefront="prod", timezone=config.timezone)

    def request(name, scopes, row_type, handler, *, many=True, required_read=True):
        choices = {key: tuple(dict.fromkeys(scope[key] for scope in scopes)) for key in scopes[0]}
        model = create_model(name, __base__=PolicyModel, **{key: (Literal[values], ...) for key, values in choices.items()})
        models[name] = model
        definitions.append((model, row_type, handler, many, required_read))
        if required_read:
            required.extend((name, scope) for scope in scopes)
        return model

    measure_spec = None
    normalized_plan = None
    if case in ("revenue", "category-comparison", "promotion-review", "composed", "shipments"):
        if case == "composed":
            spec = validate_plan(plan or DEFAULT_PLAN, config)
            normalized_plan = spec.model_dump()
            measure_spec = normalized_plan
        else:
            measure_spec = dict(measure="shipped_orders" if case == "shipments" else "net_revenue",
                group_by="customer" if case == "shipments" else "category",
                periods=["/".join(previous_week(config))] if case == "shipments" else
                        [previous, month] if case in ("category-comparison", "promotion-review") else [month],
                shape="comparison" if case in ("category-comparison", "promotion-review") else "aggregate",
                top=None, region="West" if case == "promotion-review" else "all", channel="all", customer_tier="all")
        scopes = [dict(**common, metric=measure_spec["measure"], group_by=measure_spec["group_by"], period=period,
                       region=measure_spec["region"], channel=measure_spec["channel"], customer_tier=measure_spec["customer_tier"])
                  for period in measure_spec["periods"]]
        request("MeasureQuery", scopes, MeasureRow, lambda sql, req: queries.measure_rows(sql, req, config))
        output = MetricResult
    elif case == "restock":
        request("RestockQuery", [dict(**common, snapshot=config.cutoff)], RestockRow, lambda sql, req: queries.stock_rows(sql, config))
        models["RestockProposal"] = RestockProposal
        output = RestockPlan
    elif case == "refund":
        request("RefundQuery", [dict(**common, line_id=refund.line_id, requested_at=refund.requested_at)],
                RefundEvidence, lambda sql, req: queries.refund_rows(sql, req))
        output = RefundDecision
    elif case in ("loyalty", "churn"):
        cutoffs = [month + "-01", config.cutoff] if case == "loyalty" else [config.cutoff]
        request("CustomerQuery", [dict(**common, cutoff=cutoff) for cutoff in cutoffs], CustomerState,
                lambda sql, req: queries.customer_rows(sql, req.cutoff, config))
        output = CustomerList
    elif case == "product-margin":
        request("ProductMarginQuery", [dict(**common, product_id="P-120", period=month)], MarginRow,
                lambda sql, req: queries.margin_rows(sql, req, config), many=False)
        request("OptionalMarginQuery", [dict(**common, product_id="P-101", period=month)], MarginRow,
                lambda sql, req: queries.margin_rows(sql, req, config), many=False, required_read=False)
        output = ProductMargin
    elif case == "return-rate":
        request("SalesUnitsQuery", [dict(**common, period=month)], UnitsRow,
                lambda sql, req: queries.sold_units(sql, req, config), many=False)
        request("ReturnsQuery", [dict(**common, period=month, cutoff=config.cutoff)], ReturnedUnits,
                lambda sql, req: queries.returned_units(sql, req, config))
        output = ReturnRate

    requirements = ()
    claim_names = None
    if case == "promotion-review":
        promotion = request("PromotionQuery", [dict(**common, period=month, previous_period=previous, region="West")], PromotionRow,
                            lambda sql, req: queries.promotion_rows(sql, req, config))
        comparison, pause = promotion_claims(models["MeasureQuery"], promotion)
        before, after = required[0][1], required[1][1]
        sources = dict(before=before, after=after)
        requirements = (RequiredClaim(comparison, sources),
                        RequiredClaim(pause, dict(source=required[2][1], comparison=sources)))
        output = answer_model((comparison, pause), required=requirements)
        models.update(CategoryComparisonClaim=comparison, PromotionPauseClaim=pause)
        claim_names = frozenset(("CategoryComparisonClaim", "PromotionPauseClaim", "Answer"))
    models.update({output.__name__: output, "Abstain": Abstain})
    # Include nested output constructors needed to calculate an answer in Monty.
    if case in ("revenue", "shipments", "category-comparison", "composed", "promotion-review"):
        models["MeasureRow"] = MeasureRow

    def operations(sql):
        return [Operation(model, row_type, lambda req, handler=handler: handler(sql, req),
                          many=many, required=needed) for model, row_type, handler, many, needed in definitions]

    inputs = {"store_config": Input(dict, config.model_dump())}
    if case in ("loyalty", "churn"):
        windows = {cutoff: {"start": utc_boundary((date.fromisoformat(cutoff) - timedelta(days=config.active_days)).isoformat(), config.timezone),
                            "end": utc_boundary(cutoff, config.timezone)} for cutoff in cutoffs}
        cutoff_date = date.fromisoformat(config.cutoff)
        windows["churn"] = {
            "start": utc_boundary((cutoff_date - timedelta(days=config.active_days + 89)).isoformat(), config.timezone),
            "end": utc_boundary((cutoff_date - timedelta(days=config.active_days - 1)).isoformat(), config.timezone),
        }
        inputs["customer_windows"] = Input(dict, windows)
    if case == "refund":
        inputs["refund_request"] = Input(dict, refund.model_dump())
    helpers = (required_requests, observed, compare_rows, measure_result, eligible_stock, restock_reason,
               refund_decision, customer_selection, gold_customer, active_customer, local_date, pause_rows, claim_input)
    if case == "promotion-review":
        helpers += (Claim, settle, AnswerEnvelope)
    contract = render_contract(operations(None), models, inputs, extra_sources=helpers)
    if case == "promotion-review":
        contract = "from typing import ClassVar\nfrom agent_pathcheck.extensions.claims import PLACEHOLDER\n" + contract
    # Identity includes behavior-affecting configuration, dependency declarations,
    # scope and required claims, even where validators call host-only helpers.
    identity = dict(case=case, config=config.model_dump(), required=required, plan=normalized_plan,
                    refund=refund.model_dump() if case == "refund" else None,
                    dependencies=dependencies(spec) if normalized_plan else {},
                    required_claims=[item.description() for item in requirements])
    contract += "\n# Host configuration: " + json.dumps(identity, sort_keys=True) + "\n"

    @contextmanager
    def open_invocation(report, initialize):
        report["queries"] = []
        with ExitStack() as stack:
            database = None

            def sql(statement, params=()):
                nonlocal database
                if database is None:
                    try:
                        database = stack.enter_context(open_database(initialize, report["queries"]))
                    except sqlite3.IntegrityError as exc:
                        raise DataBlocked("data.integrity", str(exc)) from None
                return database(statement, params)

            query = bind_operations(operations(sql), report["policy_calls"], report["policy_events"])
            yield Invocation({name: item.value for name, item in inputs.items()}, {"query": query}, models,
                             lambda value: validate_model_output(value, output, report["policy_events"]), claim_names)

    return Policy("online-store-" + case + "-" + policy, "1", open_invocation, output,
                  dict(case=case, config=config, refund=refund, models=models, required_specs=required,
                       operations=operations, measure_spec=measure_spec, normalized_plan=normalized_plan,
                       plan_summary=describe_plan(spec, config) if normalized_plan else None,
                       dependencies=identity["dependencies"], request_types=tuple(item[0] for item in definitions),
                       required_claims=requirements), contract, required_requests=required_requests, rules=rules_for(config))

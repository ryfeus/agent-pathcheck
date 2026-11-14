"""Generate and freeze code-backed semantic pages without loading fixture data."""

import argparse
from dataclasses import replace
from pathlib import Path
import tempfile

from agent_pathcheck.semantic import (Relationship, SemanticReader, Vocabulary, build_pages, check_declarations,
                                     check_pages, page_parts, policy_models, write_pages)
from . import models
from .data import TABLE_MODELS
from .policy import CASES, build_policy


# Authored explanations are shared across policy snapshots. Generated schema,
# validators, identities and active scope still come from the compiled policy.
MEANING = {
    "Customer": "The purchaser owns orders and returns. Active status is evaluated from qualifying production orders at the report snapshot; Gold additionally uses rolling net revenue.",
    "Order": "A purchase links one customer to its lines. Production scope and qualifying payment statuses are inherited by every measure. Store-local boundaries select canonical UTC timestamps.",
    "OrderLine": "A stable line identifier distinguishes repeated products within an order. Discounts are line totals, and paid amount is quantity times unit price less discount.",
    "Product": "The catalog supplies category, supplier, status and nullable unit cost. Discontinued products cannot be restock proposals. Missing cost is unknown, not zero.",
    "Category": "Categories retain their identity when sales appear or disappear. Comparisons use the union of both observed periods.",
    "Shipment": "One shipment belongs to an order. A shipment can be overdue before it is delivered; historical reporting observes delivery only through its cutoff.",
    "Return": "A return reverses quantities and money on one specific line owned by its requesting customer. It follows delivery, and cumulative returns cannot exceed that line's paid amount or quantity.",
    "InventorySnapshot": "Inventory identifies one product, warehouse and local date. Product inventory sums warehouses only when every known warehouse has the required snapshot.",
    "Supplier": "Supplier status gates restocking and lead time is compared with product-specific stock cover. A supplier on a late order is associated with that delay, not established as its cause.",
    "Promotion": "The offered discount is stored as integer basis points. Assessment compares it with the category's realized margin percentage over the promotion's overlap with the report period and region.",
    "PromotionRow": "The host measures promotions on declining categories within the exact regional comparison. Missing margin in an unrelated rising category is not a prerequisite. The final claim separately verifies the join to observed category changes.",
    "Forecast": "A dated synthetic forecast supplies projected lost revenue for a product. The example checks its identity and ranking use, not the forecasting method.",
    "RestockRow": "A product's stock, sales history, velocity, margin inputs and dated forecast stay on the same product. Unknown required facts block the whole read before it becomes evidence.",
    "CustomerState": "This complete customer snapshot contains last qualifying order, lifetime order count and rolling annual net revenue. Both loyalty and churn use the same activity definition.",
    "RefundEvidence": "The host joins the requested line to its actual owner and delivery, then subtracts returns observed by the request time. Delivery age is measured in store-local calendar days.",
    "ReturnedUnits": "Each row is a return against the selected order cohort observed before the cutoff. Returns of older orders affect revenue, but not this cohort's return rate.",
    "ReturnRate": "Numerator and denominator refer to the same order cohort. A completed zero-sales cohort has an undefined rate; a truncated read cannot support any rate.",
    "RestockPlan": "The complete plan contains the highest-ranked eligible products, one entry per product, plus their exact combined forecast. An empty plan requires completed evidence.",
    "RestockProposal": "This is an eligibility filter, not a final claim. Rejecting an ineligible candidate is allowed while discovering the complete valid ranking.",
    "RefundDecision": "The checked outcome is rejection, automatic approval eligibility, or human review. It does not issue a refund.",
    "MetricResult": "The host recomputes all groups and their sum. Comparisons include absent groups as zero; top selections use deterministic ordering and their selected sum.",
    "CategoryComparisonClaim": "The claim names both exact completed source requests. Its ordered differences are recomputed from their union.",
    "PromotionPauseClaim": "This claim depends on both a completed promotion assessment and a checked category comparison. Only overspending promotions in declining categories can appear.",
    "Answer": "Required claims fix the sources and the verified category join. Templates substitute checked values; causal meaning and other narrative remain unverified.",
    "Abstain": "Refusal requires a matching unresolved block on an exact task-required request. Optional, unrelated or recovered reads cannot authorize refusal.",
}


def configuration(bundle):
    documented = replace(bundle, parameters={**bundle.parameters,
        "models": {**{model.__name__: model for model in TABLE_MODELS.values()}, **bundle.parameters["models"]}})
    relationships = [
        Relationship("order_customer", models.Order, "customer_id", models.Customer, "customer_id", "An order belongs to its purchasing customer."),
        Relationship("line_order", models.OrderLine, "order_id", models.Order, "order_id", "A line belongs to its exact order."),
        Relationship("line_product", models.OrderLine, "product_id", models.Product, "product_id", "History and costs belong to this product."),
        Relationship("product_category", models.Product, "category_id", models.Category, "category_id", "A product belongs to a category."),
        Relationship("product_supplier", models.Product, "supplier_id", models.Supplier, "supplier_id", "A product's supplier supplies status and lead time."),
        Relationship("shipment_order", models.Shipment, "order_id", models.Order, "order_id", "The delivery date belongs to this order."),
        Relationship("return_line", models.Return, "line_id", models.OrderLine, "line_id", "The return reverses this specific line, not a sibling line."),
        Relationship("return_customer", models.Return, "customer_id", models.Customer, "customer_id", "The return's customer must also own its line's order."),
        Relationship("inventory_product", models.InventorySnapshot, "product_id", models.Product, "product_id", "Stock is scoped to this product, warehouse and date."),
        Relationship("forecast_product", models.Forecast, "product_id", models.Product, "product_id", "Forecast and inventory must use the same product and snapshot date."),
        Relationship("promotion_category", models.Promotion, "category_id", models.Category, "category_id", "The pause list joins to the same declining category."),
    ]
    operations = bundle.parameters["operations"](None)
    vocabulary = [Vocabulary("production", operation.request_type, "storefront", "prod") for operation in operations]
    return documented, relationships, vocabulary, operations


def pages_for(bundle):
    configuration_args = configuration(bundle)
    pages = build_pages(*configuration_args)
    for name, text in list(pages.items()):
        prefix, _ = page_parts(text)
        title = prefix.split("\n# ", 1)[1].split("\n", 1)[0]
        rule = bundle.rules.get(title)
        meaning = rule.why if rule else MEANING.get(title)
        if meaning is None:
            if name.startswith("measures/"):
                meaning = "This typed request fixes the active task's scope. Its complete validated result batch must commit before a dependent claim is constructed."
            elif name.startswith("relationships/"):
                meaning = next(item.description for item in configuration_args[1] if item.name == title)
            else:
                meaning = "This page describes the active store contract. Fields and checked rules are derived from the same models used during execution."
        target = ("" if name.startswith("rules/") else "../rules/" if "/" in name else "rules/") + (title if rule else "store.scope") + ".md"
        pages[name] = (prefix + "\n\n## Meaning\n\n" + meaning +
                       "\n\n## Notes (advice, not checked)\n\nDocumentation guides interpretation and never commits evidence. Narrative explanations remain unverified.\n" +
                       "\n## Rationale\n\nSee the [applicable rule](" + target + ").\n")
    return pages


def snapshot(bundle):
    # SemanticReader copies all pages before the temporary directory is removed.
    # A server keeps that immutable snapshot for the lifetime of its policy.
    with tempfile.TemporaryDirectory(prefix="online-store-semantic-") as directory:
        write_pages(directory, pages_for(bundle))
        return SemanticReader(directory)


def check(bundle, directory=None):
    args = configuration(bundle)
    problems = check_declarations(args[1], args[2], policy_models(args[0], args[3]).values(), {})
    if directory is None:
        with tempfile.TemporaryDirectory(prefix="online-store-check-") as temporary:
            write_pages(temporary, pages_for(bundle))
            return problems + check(bundle, temporary)
    problems += check_pages(directory, *args)
    if len((Path(directory) / "index.md").read_text()) > 3000:
        problems.append("Semantic index exceeds the tool-description budget")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", action="store_true")
    action.add_argument("--check", action="store_true")
    parser.add_argument("--case", choices=CASES)
    parser.add_argument("--output", type=Path, help="Directory of materialized case trees; omit to check fresh snapshots")
    args = parser.parse_args()
    problems = []
    for case in ([args.case] if args.case else CASES):
        bundle = build_policy(case)
        if args.write:
            output = args.output or Path(__file__).with_name("results") / "local" / "semantic"
            write_pages(output / case, pages_for(bundle))
        else:
            problems.extend(f"{case}: {problem}" for problem in check(bundle, args.output / case if args.output else None))
    print(json_summary(args.write, problems))
    raise SystemExit(bool(problems))


def json_summary(written, problems):
    import json
    return json.dumps({"action": "write" if written else "check", "problems": problems}, indent=2)


if __name__ == "__main__":
    main()

"""Business boundaries and adversarial workflows against real SQLite/Monty."""

import unittest
from unittest.mock import patch

from agent_pathcheck import DataBlocked
from agent_pathcheck.extensions.engine import Engine
from examples.online_store import queries, rules, policy as policy_module
from examples.online_store.acceptance import check_probe, correction_impact, negative_probes
from examples.online_store.concepts import StoreConfig, affected_policies, utc_boundary
from examples.online_store.counterfactuals import replays_for
from examples.online_store.models import RefundRequest
from examples.online_store.policy import CASES, build_policy
from examples.online_store.tracer import run_checked
from examples.online_store.workflows import request_code, workflow


class StoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = Engine()
        cls.engine.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.engine.__exit__(None, None, None)

    def execute(self, case, code=None, **options):
        build_options = {key: options[key] for key in ("policy", "config", "plan", "refund") if key in options}
        code = code if code is not None else workflow(case, **build_options)
        return run_checked(code, case=case, engine=self.engine, **options)

    def passed(self, report):
        self.assertEqual(report["validation_status"], "passed", report["errors"])
        return report["result"]

    def failed(self, report, rule=None):
        self.assertNotEqual(report["validation_status"], "passed", report)
        self.assertIsNone(report["validated_answer"])
        if rule:
            self.assertIn(rule, str(report))

    def read(self, case, **options):
        return "query(" + request_code(build_policy(case, **options), 0) + ")"

    def test_all_reference_results_have_independent_oracles(self):
        for case in CASES:
            with self.subTest(case=case):
                report = self.execute(case)
                self.passed(report)
                self.assertEqual(report["answer_check"], "passed", report)
                self.assertTrue(report["queries"])
                self.assertEqual(report["validated_answer"]["basis"], "domain_contract")

    def test_scope_and_raw_sql_fail_before_database_reads(self):
        request = self.read("revenue")
        for old, new in (("'prod'", "'test'"), ("'net_revenue'", "'gross_revenue'"),
                         ("'2026-10'", "'2026-09'"), ("'America/Los_Angeles'", "'UTC'")):
            report = self.execute("revenue", request.replace(old, new))
            self.failed(report, "literal_error")
            self.assertEqual(report["queries"], [])
        for code in ('query("SELECT * FROM orders")', 'query({"storefront":"prod"})'):
            report = self.execute("revenue", code)
            self.failed(report)
            self.assertEqual(report["queries"], [])
        mutated = request.replace("query(", "q = ", 1)[:-1] + '\nq.storefront = "test"\nquery(q)'
        report = self.execute("revenue", mutated)
        self.failed(report)
        # Monty may shadow an eager guest attribute. The host receives its
        # original frozen request; such a shadow cannot select test data.
        for operation in report["policy_calls"]:
            self.assertEqual(operation["request"]["storefront"], "prod")
        for entry in report["queries"]:
            self.assertIn("o.storefront='prod'", entry["sql"])
            self.assertEqual(sum(row["value"] for row in entry["rows"]),36200000)

    def test_guest_parameters_and_final_claim_budget_cannot_weaken_policy(self):
        code = 'store_config["restock_size"] = 1\n' + workflow("restock")
        self.failed(self.execute("restock",code),"store.restock_plan")
        code = workflow("revenue").replace("MetricResult(rows=rows,", "first = MetricResult(rows=rows,")
        code += '\ntry:\n    MetricResult(rows=rows,total=sum(r.value for r in rows))\nexcept ValueError:\n    pass\nfirst'
        self.failed(self.execute("revenue",code),"claim.attempt_limit")

    def test_category_comparison_ties_use_names_and_restock_sums_warehouses(self):
        sql = """
          UPDATE order_lines SET unit_price_cents=unit_price_cents-2400000 WHERE line_id='L-balance-2026-10-Apparel';
          UPDATE order_lines SET unit_price_cents=unit_price_cents-1700000 WHERE line_id='L-balance-2026-10-Electronics';
          UPDATE categories SET name='Zulu apparel' WHERE category_id='Apparel';
          UPDATE categories SET name='Alpha electronics' WHERE category_id='Electronics';
        """
        rows = self.passed(self.execute("category-comparison",extra_sql=sql))["rows"]
        self.assertEqual([row["label"] for row in rows if row["value"] == 100000],["Alpha electronics","Zulu apparel"])
        sql = "INSERT INTO inventory VALUES ('P-101','W-2','2026-11-01',100,1);"
        self.assertNotIn({"product_id":"P-101"},self.passed(self.execute("restock",extra_sql=sql))["proposals"])

    def test_month_edges_and_refund_event_dates(self):
        # A UTC October timestamp before local midnight still belongs to September.
        sql = """
          INSERT INTO orders VALUES ('edge','C-balance','2026-10-01T06:59:59+00:00','prod','web','paid');
          INSERT INTO order_lines VALUES ('edge-line','edge','B-Books',1,123,0);
        """
        self.assertEqual(self.passed(self.execute("revenue", extra_sql=sql))["total"], 36200000)
        at_boundary = sql.replace("06:59:59", "07:00:00")
        self.assertEqual(self.passed(self.execute("revenue", extra_sql=at_boundary))["total"], 36200123)
        moved_refund = "UPDATE returns SET returned_at='2026-11-01T07:00:00+00:00' WHERE return_id='R-prior-order';"
        self.assertEqual(self.passed(self.execute("revenue", extra_sql=moved_refund))["total"], 36210000)
        self.assertEqual(utc_boundary("2026-11-01", "America/Los_Angeles"), "2026-11-01T07:00:00+00:00")
        self.assertEqual(utc_boundary("2026-12-01", "America/Los_Angeles"), "2026-12-01T08:00:00+00:00")

    def test_negative_probes_and_strict_money(self):
        for probe in negative_probes():
            with self.subTest(probe=probe["name"]):
                self.assertTrue(check_probe(probe, engine=self.engine)["passed"])
        for value in ("True", "36200000.0"):
            code = workflow("revenue").replace("total=sum(r.value for r in rows)", "total=" + value)
            self.failed(self.execute("revenue", code), "int_type")

    def test_comparison_rejects_fabricated_breakdown_with_correct_total(self):
        code = workflow("category-comparison").replace("value=right.get(k, 0)-left.get(k, 0)",
            'value=right.get(k, 0)-left.get(k, 0)+(1 if k == "Apparel" else -1 if k == "Electronics" else 0)')
        self.failed(self.execute("category-comparison", code), "store.metric_result")

    def test_empty_completed_reads_differ_from_missing_reads(self):
        report = self.execute("revenue", extra_sql="UPDATE orders SET storefront='test';")
        self.assertEqual(self.passed(report), {"rows": [], "total": 0})
        self.failed(self.execute("revenue", "MetricResult(rows=[], total=0)"), "evidence.required")
        report = self.execute("restock", extra_sql="UPDATE orders SET storefront='test';")
        self.assertEqual(self.passed(report)["outcome"], "no_eligible_products")
        self.failed(self.execute("restock", 'RestockPlan(proposals=[],total_lost_revenue_cents=0,outcome="no_eligible_products")'), "evidence.required")

    def test_invalid_result_batch_is_never_evidence(self):
        original = queries.measure_rows
        def malformed(*args):
            rows = original(*args)
            rows[-1]["value"] = "not cents"
            return rows
        code = f'try:\n    {self.read("revenue")}\nexcept ValueError:\n    pass\nMetricResult(rows=[], total=0)'
        with patch.object(queries, "measure_rows", malformed):
            report = self.execute("revenue", code)
        self.failed(report, "evidence.required")
        self.assertEqual(report["policy_calls"][0]["result_check"], "failed")

    def test_final_revalidation_rejects_stale_model(self):
        original = queries.measure_rows
        calls = []
        def changing(*args):
            rows = original(*args)
            if calls:
                rows[0]["value"] += 1
            calls.append(True)
            return rows
        code = workflow("revenue").replace("MetricResult(rows=rows,", "answer = MetricResult(rows=rows,")
        code += "\n" + self.read("revenue") + "\nanswer"
        with patch.object(queries, "measure_rows", changing):
            self.failed(self.execute("revenue", code), "store.metric_result")

    def test_failed_queries_and_invocations_cannot_supply_evidence(self):
        self.passed(self.execute("revenue"))
        self.failed(self.execute("revenue", "MetricResult(rows=[],total=0)"), "evidence.required")
        code = f'try:\n    {self.read("revenue")}\nexcept ValueError:\n    pass\nMetricResult(rows=[],total=0)'
        with patch.object(queries, "measure_rows", side_effect=ValueError("upstream query failed")):
            self.failed(self.execute("revenue", code), "evidence.required")

    def test_refund_exact_entity_dates_amount_and_quantity_boundaries(self):
        for changes, decision, reason in (
            ({}, "auto_approve", "within_policy"),
            ({"requested_at":"2026-11-01T20:00:00+00:00"}, "rejected", "window"),
            ({"requested_at":"2026-09-30T19:00:00+00:00"}, "rejected", "before_delivery"),
            ({"customer_id":"C-new"}, "rejected", "ownership"),
            ({"order_id":"weekly"}, "rejected", "ownership"),
            ({"line_id":"L-weekly"}, "rejected", "ownership"),
            ({"refund_cents":50001}, "human_review", "amount_over_limit"),
            ({"refund_cents":190001}, "rejected", "remaining_amount_or_quantity"),
            ({"quantity":2}, "rejected", "remaining_amount_or_quantity"),
        ):
            with self.subTest(changes=changes):
                refund = RefundRequest(**{**RefundRequest().model_dump(), **changes})
                result = self.passed(self.execute("refund", refund=refund))
                self.assertEqual(result, {"decision":decision,"reason":reason})
        refund = RefundRequest(customer_id="C-new")
        forged = self.read("refund", refund=refund) + '\nRefundDecision(decision="auto_approve", reason="within_policy")'
        self.failed(self.execute("refund", forged, refund=refund), "store.refund")

    def test_source_integrity_refuses_invalid_returns(self):
        statements = [
            "UPDATE returns SET customer_id='C-new' WHERE return_id='R-prior-order';",
            "UPDATE returns SET returned_at='2026-09-30T00:00:00+00:00' WHERE return_id='R-prior-order';",
            "UPDATE returns SET refund_cents=200001 WHERE return_id='R-prior-order';",
            "UPDATE returns SET quantity=3 WHERE return_id='R-prior-order';",
            "UPDATE order_lines SET discount_cents=100000000 WHERE line_id='L-1042';",
            "UPDATE products SET supplier_id='invented' WHERE product_id='P-101';",
        ]
        for sql in statements:
            with self.subTest(sql=sql):
                self.failed(self.execute("revenue", extra_sql=sql), "data.integrity")

    def test_restock_disqualifiers_sibling_history_and_ranking(self):
        for product in ("P-103", "P-104", "P-105", "P-106", "P-113", "invented"):
            code = self.read("restock") + f'\nRestockProposal(product_id={product!r})'
            self.failed(self.execute("restock", code), "store.restock_eligible")
        for products in (["P-101","P-102","P-108","P-107","P-109"],
                         ["P-101","P-101","P-107","P-108","P-109"],
                         ["P-101","P-102","P-107","P-108","P-110"]):
            code = self.read("restock") + f'\nRestockPlan(proposals=[{{"product_id":p}} for p in {products!r}],total_lost_revenue_cents=1310000,outcome="recommendations")'
            self.failed(self.execute("restock", code), "store.restock_plan")

    def test_restock_history_velocity_cover_and_correction_boundaries(self):
        history29 = "UPDATE orders SET placed_at='2026-08-01T19:00:00+00:00' WHERE order_id='H-P-108-0';"
        self.assertNotIn({"product_id":"P-108"}, self.passed(self.execute("restock", extra_sql=history29))["proposals"])
        self.assertIn({"product_id":"P-108"}, self.passed(self.execute("restock"))["proposals"])
        zero_velocity = "UPDATE orders SET placed_at='2026-09-30T19:00:00+00:00' WHERE order_id='M-P-108';"
        self.assertNotIn({"product_id":"P-108"}, self.passed(self.execute("restock", extra_sql=zero_velocity))["proposals"])
        cover = "UPDATE inventory SET on_hand=14,reorder_point=15 WHERE product_id='P-108';"
        self.assertNotIn({"product_id":"P-108"}, self.passed(self.execute("restock", extra_sql=cover))["proposals"])
        for sales, included in ((4900,False),(5000,True)):
            sql = f"UPDATE order_lines SET discount_cents={6000-sales} WHERE line_id='L-M-P-109';"
            selected = self.passed(self.execute("restock", policy="minimum-monthly-sales", extra_sql=sql))["proposals"]
            self.assertEqual({"product_id":"P-109"} in selected, included)

    def test_missing_potential_candidate_facts_block_whole_read(self):
        for sql, rule in (
            ("UPDATE products SET unit_cost_cents=NULL WHERE product_id='P-101';", "store.cost_missing"),
            ("UPDATE forecasts SET date='2026-10-31' WHERE product_id='P-101';", "store.forecast_missing"),
            ("INSERT INTO inventory VALUES ('P-101','W-2','2026-10-31',1,10);", "store.inventory_missing"),
        ):
            code = f'try:\n    {self.read("restock")}\nexcept ValueError:\n    pass\nAbstain(kind="data_block",rule_id={rule!r},detail="Required candidate evidence is missing.")'
            result = self.passed(self.execute("restock", code, extra_sql=sql))
            self.assertEqual(result["rule_id"], rule)
        # P-120 cannot qualify with one selling day, so its missing inventory,
        # cost and forecast are not prerequisites for selecting other products.
        sql = "DELETE FROM inventory WHERE product_id='P-120'; DELETE FROM forecasts WHERE product_id='P-120';"
        self.assertEqual(self.passed(self.execute("restock", extra_sql=sql))["total_lost_revenue_cents"],1310000)

    def test_shared_active_window_changes_both_customer_policies_and_identity(self):
        for days, loyalty, churn in ((90,["C-future","C-new","C-window"],["C-churn"]),
                                     (60,["C-future","C-new"],["C-churn","C-window","C-not-gold"])):
            config = StoreConfig(active_days=days)
            self.assertEqual(self.passed(self.execute("loyalty", config=config))["customer_ids"], loyalty)
            self.assertEqual(self.passed(self.execute("churn", config=config))["customer_ids"], churn)
        for case in ("loyalty","churn","composed"):
            self.assertNotEqual(build_policy(case).policy_hash, build_policy(case,config=StoreConfig(active_days=60)).policy_hash)
        self.assertEqual(affected_policies("active_customer"), ["loyalty","churn","composed:customer_tier=gold"])

    def test_abstention_requires_unresolved_exact_required_block(self):
        self.assertEqual(self.passed(self.execute("product-margin"))["kind"], "abstained")
        unjustified = 'Abstain(kind="data_block",rule_id="store.cost_missing",detail="No cost")'
        self.failed(self.execute("product-margin", unjustified), "abstain.unjustified")
        bundle = build_policy("product-margin")
        optional = bundle.parameters["models"]["OptionalMarginQuery"]
        scope = {name: field.annotation.__args__[0] for name, field in optional.model_fields.items()}
        code = f'try:\n    query(OptionalMarginQuery(**{scope!r}))\nexcept ValueError:\n    pass\n{unjustified}'
        self.failed(self.execute("product-margin", code, extra_sql="UPDATE products SET unit_cost_cents=NULL WHERE product_id='P-101';"), "abstain.unjustified")
        attempts = []
        def recover(*_):
            if not attempts:
                attempts.append(1)
                raise DataBlocked("store.cost_missing", "temporarily unavailable")
            return [{"product_id":"P-120","net_revenue_cents":10000,"cost_cents":100}]
        code = f'try:\n    {self.read("product-margin")}\nexcept ValueError:\n    pass\n{self.read("product-margin")}\n{unjustified}'
        with patch.object(queries, "margin_rows", recover):
            self.failed(self.execute("product-margin", code), "abstain.unjustified")

    def test_returns_truncation_zero_sales_and_mismatched_cohort(self):
        report = self.execute("return-rate", fixture_name="truncated-returns")
        self.assertEqual(self.passed(report)["rule_id"], "limit.max_rows")
        self.assertEqual(report["policy_calls"][-1]["result_check"], "failed")
        code = workflow("return-rate").replace('result = Abstain(kind="resource_limit", rule_id="limit.max_rows", detail="The required cohort returns exceeded the row limit.")',
                                               'result = ReturnRate(sold_units=543,returned_units=2,rate=2/543)')
        self.failed(self.execute("return-rate", code, fixture_name="truncated-returns"), "evidence.required")
        result = self.passed(self.execute("return-rate", extra_sql="UPDATE orders SET storefront='test';"))
        self.assertEqual(result, {"sold_units":0,"returned_units":0,"rate":None})
        code = workflow("return-rate").replace("count = sum(r.units for r in returned)", "count = sum(r.units for r in returned)+1")
        self.failed(self.execute("return-rate", code), "store.return_rate")

    def test_deliberate_query_budget_exhaustion_is_not_abstention(self):
        code = f'for i in range(11):\n    try:\n        {self.read("revenue")}\n    except ValueError:\n        pass\nAbstain(kind="resource_limit",rule_id="limit.max_queries",detail="Too many queries")'
        self.failed(self.execute("revenue", code), "abstain.unjustified")

    def test_correction_and_weakened_rule_negative_probe(self):
        self.assertTrue(correction_impact(engine=self.engine)["passed"])
        probe = negative_probes()[-1]
        original = rules.restock_reason
        def weakened(row, config):
            return original(row, StoreConfig(**{**config.model_dump(), "minimum_monthly_sales_cents":0}))
        with patch.object(rules, "restock_reason", weakened), patch.object(policy_module, "restock_reason", weakened):
            self.assertFalse(check_probe(probe, engine=self.engine)["passed"])

    def test_counterfactuals_reject_stale_totals_and_selections(self):
        for case in ("revenue", "restock"):
            report = self.execute(case, replays=replays_for(case), require_replays=True)
            self.passed(report)
            self.assertEqual(report["replay_check"], "passed")
        code = workflow("revenue").replace("total=sum(r.value for r in rows)", "total=36200000")
        self.failed(self.execute("revenue", code, replays=replays_for("revenue"), require_replays=True), "replay.required")
        code = self.read("restock").replace("query(", "rows = query(") + '''
ids = ["P-101","P-102","P-107","P-108","P-109"]
RestockPlan(proposals=[{"product_id":p} for p in ids],total_lost_revenue_cents=sum(r.lost_revenue_cents for r in rows if r.product_id in ids),outcome="recommendations")'''
        self.failed(self.execute("restock", code, replays=replays_for("restock"), require_replays=True), "replay.required")


if __name__ == "__main__":
    unittest.main()

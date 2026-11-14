"""Planner compositions, shared filters, and evidence-linked promotion claims."""

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from agent_pathcheck.extensions.engine import Engine
from agent_pathcheck.semantic import SemanticReader, write_pages
from examples.online_store.concepts import StoreConfig
from examples.online_store.planner import DEFAULT_PLAN, PlanSpec, compile_plan, plan_question
from examples.online_store.policy import build_policy
from examples.online_store.semantic import check, pages_for, snapshot
from examples.online_store.tracer import run_checked
from examples.online_store.workflows import workflow


def call(name, **arguments):
    return SimpleNamespace(status="completed", usage=None, output_text="", output=[
        SimpleNamespace(type="function_call", name=name, arguments=json.dumps(arguments), call_id="test-call")])


def final(text="Narrative is unverified."):
    return SimpleNamespace(status="completed", usage=None, output=[], output_text=text)


def api_for(*responses):
    return SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(side_effect=responses)))


class CompositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = Engine()
        cls.engine.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.engine.__exit__(None, None, None)

    def execute(self, case="composed", code=None, **options):
        code = workflow(case, **{key: options[key] for key in ("plan","config") if key in options}) if code is None else code
        return run_checked(code, case=case, engine=self.engine, **options)

    def test_compiler_supports_multiple_compositions_without_new_validators(self):
        specs = [
            dict(measure="net_revenue",group_by="category",periods=["2026-10"]),
            dict(measure="net_revenue",group_by="category",periods=["2026-09","2026-10"],shape="comparison"),
            dict(measure="net_revenue",group_by="customer",periods=["2026-10"],shape="top_k",top=2,region="West"),
            dict(measure="shipped_orders",group_by="customer",periods=["2026-10"],channel="app"),
            dict(measure="late_deliveries",group_by="customer",periods=["2026-Q3"],customer_tier="gold"),
        ]
        expected = [36200000,5900000,2000000,0,2]
        for plan, total in zip(specs, expected):
            with self.subTest(plan=plan):
                bundle = compile_plan(plan)
                self.assertEqual(bundle.output_model.__name__, "MetricResult")
                report = self.execute(plan=plan)
                self.assertEqual(report["validation_status"], "passed", report["errors"])
                self.assertEqual(report["result"]["total"], total)
                self.assertEqual(report["context"]["normalized_plan"], PlanSpec(**plan).model_dump())
                self.assertEqual(report["context"]["interpretation_status"], "unverified")

    def test_plans_cannot_weaken_inherited_rules_or_add_executable_code(self):
        for changes in ({"storefront":"test"}, {"timezone":"UTC"}, {"sla_days":100}, {"sql":"SELECT * FROM orders"},
                        {"validators":[]}, {"measure":"gross_revenue"}, {"group_by":"category"},
                        {"top":True}, {"top":0}, {"periods":["2026-12"]}, {"periods":["2026-10","2026-09"],"shape":"comparison","top":None}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                compile_plan({**DEFAULT_PLAN, **changes})
        bundle = compile_plan(DEFAULT_PLAN)
        self.assertIn("store.gold", bundle.parameters["dependencies"]["rules"])
        self.assertIn("store.active_customer", bundle.parameters["dependencies"]["rules"])
        self.assertIn("Shipment→Order→Customer", bundle.parameters["dependencies"]["relationships"])

    def test_quarter_end_gold_undelivered_sla_and_supplier_deduplication(self):
        result = self.execute()["result"]
        self.assertEqual([(r["key"],r["value"]) for r in result["rows"]],[("S-1",2),("S-2",1)])
        # Changing a future delivery cannot change the historical overdue status.
        sql = "UPDATE shipments SET delivered_at='2026-12-01T20:00:00+00:00' WHERE order_id='q3-not-yet-late';"
        self.assertEqual(self.execute(extra_sql=sql)["result"], result)
        # Day five is not late. The base missing delivery was six days old.
        sql = "UPDATE shipments SET shipped_at='2026-09-26T19:00:00+00:00' WHERE order_id='q3-overdue';"
        self.assertEqual(self.execute(extra_sql=sql)["result"]["total"], 2)
        # This customer's later October spending must not establish Q3 Gold.
        sql = """
          INSERT INTO orders VALUES ('future-q3','C-future','2026-09-01T19:00:00+00:00','prod','web','paid');
          INSERT INTO order_lines VALUES ('future-q3-line','future-q3','P-102',1,100,0);
          INSERT INTO shipments VALUES ('future-q3','2026-09-02T19:00:00+00:00','2026-09-20T19:00:00+00:00','test');
        """
        self.assertEqual(self.execute(extra_sql=sql)["result"], result)
        # The shared active window also reaches compiled Gold filters.
        sql = """
          UPDATE order_lines SET unit_price_cents=300000 WHERE line_id='L-churn';
          INSERT INTO shipments VALUES ('churn','2026-07-26T19:00:00+00:00','2026-08-02T19:00:00+00:00','test');
        """
        self.assertEqual(self.execute(extra_sql=sql)["result"]["total"], 4)
        self.assertEqual(self.execute(extra_sql=sql,config=StoreConfig(active_days=60))["result"]["total"], 3)

    def test_promotions_compare_rates_and_verify_declining_category_join(self):
        report = self.execute("promotion-review")
        self.assertEqual(report["validation_status"], "passed", report["errors"])
        claims = report["result"]["claims"]
        self.assertEqual([r["promo_id"] for r in claims["pauses"]["rows"]],["PROMO-app"])
        self.assertEqual(report["validated_answer"]["context"]["narrative_status"], "unverified")
        # Equality with margin does not qualify; one basis point above does.
        for bps, expected in ((1000, []), (1001, ["PROMO-app"])):
            sql = f"UPDATE promotions SET discount_bps={bps} WHERE promo_id='PROMO-app';"
            result = self.execute("promotion-review",extra_sql=sql)["result"]
            self.assertEqual([r["promo_id"] for r in result["claims"]["pauses"]["rows"]], expected)
        # A promotion ending before the current month has no relevant sales.
        sql = "UPDATE promotions SET starts_at='2026-09-01T07:00:00+00:00',ends_at='2026-10-01T07:00:00+00:00' WHERE promo_id='PROMO-app';"
        self.assertEqual(self.execute("promotion-review",extra_sql=sql)["result"]["claims"]["pauses"]["rows"], [])
        sql = "UPDATE products SET unit_cost_cents=NULL WHERE product_id='W-app';"
        blocked = self.execute("promotion-review",extra_sql=sql)
        self.assertNotEqual(blocked["validation_status"],"passed")
        self.assertIn("store.promotion_margin", str(blocked))
        # A rising category cannot contribute to the pause list. Missing cost
        # there must not become a pretext to refuse the solvable question.
        sql = "UPDATE products SET unit_cost_cents=NULL WHERE product_id='W-home';"
        unaffected = self.execute("promotion-review",extra_sql=sql)
        self.assertEqual(unaffected["validation_status"],"passed",unaffected["errors"])
        self.assertEqual([r["promo_id"] for r in unaffected["result"]["claims"]["pauses"]["rows"]],["PROMO-app"])

    def test_nested_forgery_scope_omission_and_caught_claim_rejection(self):
        good = workflow("promotion-review")
        forged = good.replace("comparison=comparison)", 'comparison=comparison, rows=[{"promo_id":"PROMO-home","category_id":"Home","discount_bps":3000,"margin_cents":20000,"net_revenue_cents":200000}])')
        programs = [
            (forged,"store.pause_join"),
            (good.replace('"pauses": pauses', '"extra": comparison').replace('{pauses}', '{extra}'),"claims.required_missing"),
            (good.replace("before=before, after=after", "before=after, after=before"),"claims.required_missing"),
            (good.replace('region=\'West\'', 'region=\'East\''),"literal_error"),
            (good.replace('text="Category changes:', 'text="Total 99. Category changes:'),"claims.text_numbers"),
        ]
        for code, rule in programs:
            with self.subTest(rule=rule):
                report = self.execute("promotion-review",code)
                self.assertNotEqual(report["validation_status"],"passed")
                self.assertIn(rule,str(report))
                self.assertIsNone(report["validated_answer"])
        prefix = good[:good.index("comparison =")]
        code = prefix + '''try:
    CategoryComparisonClaim(before=before, after=after, rows=[])
except ValueError:
    pass
''' + good[good.index("comparison ="):]
        report = self.execute("promotion-review",code)
        self.assertNotEqual(report["validation_status"],"passed")
        self.assertIn("claims.rejection_limit",str(report))

    def test_semantic_snapshot_is_code_backed_and_never_fixture_evidence(self):
        bundle = build_policy("promotion-review")
        self.assertEqual(check(bundle), [])
        reader = snapshot(bundle)
        text = "\n".join(reader.pages.values())
        for secret in ("36200000", "1310000", "PROMO-app", "C-window"):
            self.assertNotIn(secret,text)
            self.assertNotIn(secret,bundle.contract)
        self.assertLessEqual(len(reader.read("index")),3000)
        for bad in ("../fixture.py", "/etc/passwd", "unknown", "x"*257):
            with self.assertRaises(ValueError):
                reader.read(bad)
        with tempfile.TemporaryDirectory() as directory:
            write_pages(directory,pages_for(bundle))
            before = SemanticReader(directory)
            page = Path(directory)/"rules/store.scope.md"
            page.write_text(page.read_text().replace("Documentation guides interpretation", "The authored guide explains interpretation"))
            after = SemanticReader(directory)
            self.assertNotEqual(before.docs_hash,after.docs_hash)
            self.assertEqual(before.read("store.scope"), reader.read("store.scope"))
            self.assertEqual(bundle.policy_hash,build_policy("promotion-review").policy_hash)
            page.write_text(page.read_text().replace("Level: tested","Level: documented"))
            self.assertTrue(check(bundle,directory))


class NaturalLanguagePlannerTests(unittest.IsolatedAsyncioTestCase):
    async def test_planner_inherited_scope_matches_compiled_requests(self):
        plan = dict(measure="net_revenue",group_by="customer",periods=["2026-10"],
                    shape="top_k",top=2,region="West",channel="app")
        config = StoreConfig(timezone="America/New_York",active_days=60)
        api = api_for(call("submit_plan",plan_json=json.dumps(plan)))
        result = await plan_question(api,"Top two West app customers by production revenue in October",model="offline-test", config=config)
        self.assertEqual(result["status"],"planned")
        # The planner must know the scope inherited by execution. Production
        # is not a channel or an extra field for the model to invent.
        prompt = json.loads(api.responses.create.call_args.kwargs["instructions"].split("\n",1)[1])
        bundle = compile_plan(result["plan"],config=config)
        for _, request in bundle.parameters["required_specs"]:
            for field, value in prompt["inherited_scope"].items():
                self.assertEqual(request[field],value)
            self.assertEqual(request["channel"],"app")
            self.assertEqual(request["region"],"West")
        self.assertEqual(prompt["host_configuration"],config.model_dump())
        self.assertNotIn("production",prompt["schema"]["properties"]["channel"]["enum"])
        self.assertNotIn("storefront",prompt["schema"]["properties"])

    async def test_valid_plan_and_repair_use_registered_concepts_only(self):
        invalid = {**DEFAULT_PLAN,"storefront":"test"}
        api = api_for(call("submit_plan",plan_json=json.dumps(invalid)),call("submit_plan",plan_json=json.dumps(DEFAULT_PLAN)))
        result = await plan_question(api,"Which suppliers had the most late deliveries to Gold customers last quarter?", model="offline-test")
        self.assertEqual(result["status"],"planned")
        self.assertEqual(len(result["attempts"]),2)
        self.assertEqual(result["interpretation_status"],"unverified")
        self.assertEqual(result["plan"],PlanSpec(**DEFAULT_PLAN).model_dump())
        self.assertIn("production storefront only",result["summary"])
        for invocation in api.responses.create.call_args_list:
            tools = invocation.kwargs["tools"]
            self.assertEqual([t["name"] for t in tools],["submit_plan"])
            self.assertNotIn("36200000",invocation.kwargs["instructions"])

    async def test_failure_is_bounded_and_is_not_abstention(self):
        bad = call("submit_plan",plan_json=json.dumps({**DEFAULT_PLAN,"sql":"select 1"}))
        api = api_for(bad,bad,bad)
        result = await plan_question(api,"An unsupported request", model="offline-test")
        self.assertEqual(result["status"],"planning_failed")
        self.assertEqual(result["failure"],"attempt_limit")
        self.assertEqual(api.responses.create.await_count,3)
        self.assertNotIn("validated_answer",result)
        result = await plan_question(api_for(final("Unsupported question")),"Predict next year's stock price", model="offline-test")
        self.assertEqual(result["failure"],"unsupported_or_incomplete")
        self.assertEqual(len(result["attempts"]),1)
        self.assertEqual(result["model_text"],"Unsupported question")
        self.assertEqual(result["model_text_status"],"unverified")
        self.assertTrue(result["response_complete"])
        self.assertEqual(result["response_call_count"],0)

    async def test_provider_failure_is_counted_without_retaining_private_error(self):
        api = api_for(RuntimeError("private request contents"))
        result = await plan_question(api,"Production revenue by category in October", model="offline-test")
        self.assertEqual(result["status"],"planning_failed")
        self.assertEqual(result["failure"],"api_error")
        self.assertEqual(result["error"],"RuntimeError")
        self.assertEqual(len(result["attempts"]),1)
        self.assertNotIn("private request contents",json.dumps(result))


if __name__ == "__main__":
    unittest.main()

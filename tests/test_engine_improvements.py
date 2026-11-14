"""Regression tests for evidence ownership, reports, replay, and refusal boundaries."""

from contextlib import contextmanager
from dataclasses import replace
import json
from typing import Literal
import unittest

from pydantic import create_model, model_validator, ValidationError

from agent_pathcheck import (
    Domain, Evidence, ExecutionReport, Input, Invocation, Operation, Policy,
    PolicyModel, PolicyViolation, bind_operations, current_evidence, current_policy,
    read_report, render_contract, run, tool_description, validate_model_output,
)
from agent_pathcheck.extensions.engine import Engine
from agent_pathcheck.presentation import replay_requirements_met, validated_fields
from examples.aws_finops.benchmark_cases import scenarios, witness
from examples.aws_finops.tracer import COUNTERFACTUAL, replays_for, run_checked
from examples.aws_finops.test_semantics import COST_QUERIES, cost_output, EVIDENCE_QUERIES, plan_output


class Read(PolicyModel):
    key: Literal["good", "bad", "empty"]


class Row(PolicyModel):
    value: int


class Total(PolicyModel):
    value: int

    @model_validator(mode="after")
    def check(self):
        records = current_evidence().find(Read)
        if not records:
            raise PolicyViolation("prerequisite", "total.read", "Read first")
        if self.value != sum(row.value for _, rows in records for row in rows):
            raise PolicyViolation("arithmetic", "total.sum", "Wrong sum", entity="total", details={"actual": self.value})
        return self


class EngineTests(unittest.TestCase):
    def domain(self, seen=None):
        @contextmanager
        def factory(report):
            def execute(request):
                if seen is not None:
                    seen.append(current_evidence())
                return {"good": [{"value": 3}], "bad": [{"value": 1}, {"value": "bad"}], "empty": []}[request.key]
            def observe(request, values):
                self.assertTrue(current_evidence().completed(request))
            query = bind_operations([Operation(Read, Row, execute, observe)], report["policy_calls"], report["policy_events"])
            yield Invocation({}, {"query": query}, {"Read": Read, "Total": Total},
                             lambda value: validate_model_output(value, Total, report["policy_events"]))
        return Domain({}, factory)

    def test_contextvar_in_constructors_callbacks_and_final_validation(self):
        seen = []
        result = run('query(Read(key="good"))\nTotal(value=3)', domain=self.domain(seen))
        self.assertEqual(result.report["contract_check"], "passed", result.report)
        self.assertIs(seen[0], result.evidence)
        with self.assertRaises(LookupError):
            current_evidence()
        with self.assertRaises(LookupError):
            current_policy()

    def test_reused_pool_has_fresh_guest_and_evidence(self):
        with Engine() as engine:
            first = engine.run('saved = 1\nquery(Read(key="good"))\nTotal(value=3)', domain=self.domain())
            second = engine.run('saved', domain=self.domain())
            third = engine.run('Total(value=3)', domain=self.domain())
        self.assertEqual(first.report["contract_check"], "passed")
        self.assertEqual(second.report["failure"]["class"], "runtime")
        self.assertEqual(third.report["failure"]["rule_id"], "total.read")
        self.assertIsNot(first.evidence, third.evidence)

    def test_batch_commit_is_atomic_and_empty_is_completed(self):
        result = run('try:\n    query(Read(key="bad"))\nexcept ValueError:\n    pass\n'
                     'query(Read(key="empty"))\nTotal(value=0)', domain=self.domain())
        self.assertFalse(result.evidence.completed(Read(key="bad")))
        self.assertTrue(result.evidence.completed(Read(key="empty")))
        self.assertEqual(result.evidence.require(Read(key="empty")), [])
        with self.assertRaises(PolicyViolation):
            result.evidence.require(Read(key="good"))
        self.assertEqual(result.report["contract_check"], "passed", result.report)

    def test_unvalidated_host_models_cannot_skip_batch_checks(self):
        @contextmanager
        def factory(report):
            query = bind_operations([Operation(Read, Row, lambda request: [Row.model_construct(value="bad")])],
                                    report['policy_calls'], report['policy_events'])
            yield Invocation({}, {'query': query}, {'Read': Read}, lambda value: {})
        result = run('query(Read(key="good"))', domain=Domain({}, factory))
        self.assertEqual(result.report['failure']['class'], 'data_block')
        self.assertFalse(result.evidence.completed(Read(key="good")))

    def test_optional_or_recovered_blocks_do_not_justify_refusal(self):
        for required, recover in ((False, False), (True, True)):
            with self.subTest(required=required, recover=recover):
                @contextmanager
                def factory(report):
                    count = 0
                    def execute(request):
                        nonlocal count
                        count += 1
                        return [{'value': 'bad'}] if count == 1 else []
                    query = bind_operations([Operation(Read, Row, execute, required=required)],
                                            report['policy_calls'], report['policy_events'])
                    yield Invocation({}, {'query': query}, {'Read': Read}, lambda value: {})
                code = 'try:\n    query(Read(key="good"))\nexcept ValueError:\n    pass\n'
                if recover:
                    code += 'query(Read(key="good"))\n'
                code += 'Abstain(kind="schema", rule_id="pydantic.int_type", detail="bad")'
                policy = Policy("test", "1", factory, None, required_requests=lambda _: [Read(key="good")])
                report = run(code, domain=Domain({}, factory, policy=policy, allow_abstain=True)).report
                self.assertEqual(report['failure']['rule_id'], 'abstain.unjustified')

    def test_nonhashable_request_fields_and_mutation_isolation(self):
        class Request(PolicyModel):
            values: list[int]
            scope: dict[str, str]
        request = Request(values=[1], scope={"b": "2", "a": "1"})
        ledger = Evidence()
        ledger._commit(request, [Row(value=3)])
        request.values.append(2)
        original = Request(values=[1], scope={"a": "1", "b": "2"})
        self.assertTrue(ledger.completed(original))
        self.assertFalse(ledger.completed(request))
        returned = ledger.find(Request)[0][0]
        returned.values.append(99)
        self.assertTrue(ledger.completed(original))
        self.assertEqual(ledger.results(original), [Row(value=3)])
        class Keys(PolicyModel):
            values: dict[int | str, int]
        ledger._commit(Keys(values={1: 3}), [])
        self.assertFalse(ledger.completed(Keys(values={"1": 3})))

    def test_structured_violation_and_ordered_roundtrip(self):
        result = run('query(Read(key="good"))\nTotal(value=2)', domain=self.domain()).report
        self.assertEqual(result["failure"]["class"], "policy")
        self.assertEqual(result["failure"]["rule_id"], "total.sum")
        error = result["policy_events"][-1]["errors"][0]
        self.assertEqual((error["kind"], error["entity"], error["details"]), ("arithmetic", "total", {"actual": 2}))
        self.assertEqual([e["kind"] for e in result["timeline"]], ["constructor", "operation", "operation_end", "constructor"])
        restored = ExecutionReport.model_validate_json(json.dumps(result)).model_dump(mode="json", by_alias=True)
        self.assertEqual(restored, result)
        with self.assertRaises(ValidationError):
            ExecutionReport.model_validate({**result, "timeline": [{"seq": 9, "kind": "constructor", "data": {}}]})

    def test_runtime_failures_are_not_old_caught_policy_errors(self):
        code = 'try:\n    Read(key="wrong")\nexcept ValueError:\n    pass\nno_such_name'
        result = run(code, domain=self.domain()).report
        self.assertEqual(result["failure"]["class"], "runtime")
        for code, kind in [('  x=1', "syntax"), ('iter(1)', "runtime"), ('no_such_name', "runtime")]:
            self.assertEqual(run(code, domain=self.domain()).report["failure"]["class"], kind)
        result = run('query(Read(key="good"))\nTotal(value=3)', domain=self.domain(), limits={"max_suspensions": 1}).report
        self.assertEqual(result["failure"]["limit"], "max_suspensions")
        self.assertEqual(result["failure"]["configured_value"], 1)

    def test_static_source_dynamic_literals_registry_cardinality_and_hash(self):
        for many in (True, False):
            request = create_model("Scoped", __base__=PolicyModel, scope=(Literal["one"], ...))
            operation = Operation(request, Row, lambda request: [], many=many)
            source = render_contract([operation], {"Scoped": request, "Total": Total},
                                     {"scope": Input(Literal["one"], "one")})
            self.assertIn('def check(self):', source)
            self.assertIn("scope: Literal['one']", source)
            self.assertIn('-> list[Row]' if many else '-> Row', source)
            policy = Policy("test", "1", lambda report: None, Total, contract=source)
            self.assertEqual(policy.policy_hash, replace(policy).policy_hash)
            self.assertNotEqual(policy.policy_hash, replace(policy, contract=source.replace("'one'", "'two'")).policy_hash)
        with self.assertRaises(ValueError):
            render_contract([operation], {}, {})
        self.assertIn('max_suspensions: 7', tool_description("Task", source, limits={"max_suspensions": 7}))

    def test_span_exporter_and_telemetry_failure_do_not_change_verdict(self):
        spans = []
        report = run('query(Read(key="good"))\nTotal(value=3)', domain=self.domain(), span_exporter=spans.append).report
        self.assertEqual(report["contract_check"], "passed")
        self.assertEqual([item["kind"] for item in spans], ["constructor", "operation", "constructor"])
        def broken(event):
            raise RuntimeError("telemetry down")
        self.assertEqual(run('query(Read(key="good"))\nTotal(value=3)', domain=self.domain(),
                             span_exporter=broken).report["contract_check"], "passed")


class FinOpsImprovementTests(unittest.TestCase):
    def test_all_solvable_cases_pass_counterfactuals(self):
        with Engine() as engine:
            for scenario in scenarios().values():
                if scenario.blocked_by:
                    continue
                with self.subTest(scenario=scenario.name):
                    report = run_checked(witness(scenario), **scenario.host_options(), engine=engine,
                                         replays=replays_for(scenario.case), require_replays=True)
                    self.assertEqual(report["validation_status"], "passed", report["errors"])
                    self.assertTrue(replay_requirements_met(report))
                    for trial in report["replays"]:
                        if trial["verdict"] == "inconclusive":
                            self.assertTrue(trial["exemption_reason"])
                    self.assertEqual(report["validated_answer"]["context"]["policy_hash"], report["policy_hash"])
                    self.assertNotEqual(report["data_fingerprint"], report["replays"][0]["report"]["data_fingerprint"])
                    ExecutionReport.model_validate(report)

    def test_constants_pass_base_but_fail_required_replay(self):
        for case, code in (("costs", COST_QUERIES + cost_output()), ("rightsizing", EVIDENCE_QUERIES + plan_output())):
            with self.subTest(case=case):
                report = run_checked(code, case=case, replays=[COUNTERFACTUAL], require_replays=True)
                self.assertEqual(report["contract_check"], "passed")
                self.assertEqual(report["replay_check"], "failed")
                self.assertEqual(report["validation_status"], "failed")
                self.assertIsNone(report["validated_answer"])
                self.assertEqual(run_checked(code, case=case, replays=[COUNTERFACTUAL])["validation_status"], "passed")

    def test_all_five_blocked_cases_can_justify_abstention(self):
        with Engine() as engine:
            for scenario in scenarios().values():
                if not scenario.blocked_by:
                    continue
                with self.subTest(scenario=scenario.name):
                    failed = run_checked(witness(scenario), **scenario.host_options(), engine=engine)
                    failure = failed["failure"]
                    kind = "schema" if failure["class"] == "data_block" and failure["rule_id"].startswith("pydantic.") else failure["class"]
                    code = ('try:\n' + '\n'.join('    ' + line for line in witness(scenario).splitlines()) +
                            '\nexcept ValueError:\n    pass\n' +
                            f'Abstain(kind={kind!r}, rule_id={failure["rule_id"]!r}, detail="Required data unavailable")')
                    report = run_checked(code, **scenario.host_options(), engine=engine)
                    self.assertEqual(report["validation_status"], "passed", report["errors"])
                    self.assertEqual(report["validated_result"]["kind"], "abstained")
                    self.assertEqual(report["answer_check"], "not_run")
                    from examples.aws_finops.benchmark import grade_trial
                    grade = grade_trial(scenario, dict(status="completed", **validated_fields(report),
                                                       tool_calls=[{"output": report}]))
                    self.assertEqual(grade["outcome"], "justified_abstention")

    def test_abstention_requires_current_unresolved_required_block(self):
        for prefix in ('', 'try:\n    CandidatesQuery(account="wrong")\nexcept ValueError:\n    pass\n',
                       EVIDENCE_QUERIES):
            report = run_checked(prefix + 'Abstain(kind="schema", rule_id="pydantic.literal_error", detail="No")')
            self.assertEqual(report["validation_status"], "failed", report)
            self.assertEqual(report["failure"]["rule_id"], "abstain.unjustified")
        code = ('for i in range(11):\n    try:\n        query(CandidatesQuery(account=account))\n'
                '    except ValueError:\n        pass\ntry:\n    query(EvidenceQuery(account=account, metric="memory"))\n'
                'except ValueError:\n    pass\nAbstain(kind="resource_limit", rule_id="limit.max_queries", detail="Exhausted")')
        self.assertEqual(run_checked(code)["validation_status"], "failed")

    def test_historical_reports_load_without_fabricating_a_timeline(self):
        base = {"execution": "succeeded", "contract_check": "passed", "result": 6,
                "policy_events": [{"type": "SyntheticTotal", "accepted": True}],
                "policy_calls": [{"type": "SyntheticRead"}]}
        for version in (None, 1, 2):
            with self.subTest(version=version):
                raw = {**base, **({"schema_version": version} if version else {})}
                parsed = read_report(json.loads(json.dumps(raw)))
                self.assertEqual(parsed.schema_version, version or 1)
                self.assertEqual(parsed.timeline, [])
                self.assertEqual(parsed.policy_events, raw["policy_events"])
                self.assertEqual(parsed.policy_calls, raw["policy_calls"])
                self.assertEqual(parsed.result, 6)
                self.assertEqual(parsed.replay_check, "not_run")
                self.assertEqual(parsed.replays, [])
        current = read_report({**base, "schema_version": 2, "timeline": [
            {"seq": 1, "kind": "operation", "data": {"type": "SyntheticRead"}}]})
        self.assertEqual(current.timeline[0].seq, 1)
        with self.assertRaises(ValueError):
            read_report({**base, "schema_version": 2, "timeline": [
                {"seq": 2, "kind": "operation", "data": {}}]})


class LifecycleFailureTests(unittest.TestCase):
    def test_pool_startup_failure_returns_report(self):
        from unittest.mock import patch
        domain = EngineTests().domain()
        with patch('agent_pathcheck.extensions.engine.Engine.__enter__', side_effect=OSError('worker unavailable')):
            report = run('1', domain=domain).report
        self.assertEqual(report['failure']['class'], 'infrastructure')
        self.assertIsNone(validated_fields(report)['validated_answer'])

    def test_required_replay_keeps_primary_policy_failure(self):
        report = run_checked('RightsizingPlan(proposals=[], monthly_savings_cents=0)',
                             replays=[COUNTERFACTUAL], require_replays=True)
        self.assertEqual(report['failure']['class'], 'policy')
        self.assertEqual(report['failure']['rule_id'], 'rightsizing.candidates_required')

    def test_required_replays_without_trials_cannot_certify(self):
        report = run_checked(COST_QUERIES + cost_output(), case='costs', require_replays=True)
        self.assertEqual(report['replay_check'], 'failed')
        self.assertIsNone(report['validated_answer'])

    def test_stricter_domain_abstention_check_can_reject(self):
        from agent_pathcheck import DataBlocked
        @contextmanager
        def factory(report):
            def execute(request):
                raise DataBlocked('input.unavailable', 'missing')
            query = bind_operations([Operation(Read, Row, execute)], report['policy_calls'], report['policy_events'])
            yield Invocation({}, {'query': query}, {'Read': Read}, lambda value: {})
        def stricter(value):
            raise PolicyViolation('prerequisite', 'domain.manual_review', 'Needs review')
        policy = Policy("test", "1", factory, None, required_requests=lambda _: [Read(key="good")])
        domain = Domain({}, factory, policy=policy, allow_abstain=True, check_abstention=stricter)
        code = ('try:\n    query(Read(key="good"))\nexcept ValueError:\n    pass\n'
                'Abstain(kind="data_block", rule_id="input.unavailable", detail="missing")')
        report = run(code, domain=domain).report
        self.assertEqual(report['failure']['rule_id'], 'domain.manual_review')
        self.assertIsNone(validated_fields(report)['validated_answer'])


if __name__ == "__main__":
    unittest.main()

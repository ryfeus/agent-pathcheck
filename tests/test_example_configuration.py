"""Public examples require explicit model and artifact provenance inputs."""

from contextlib import redirect_stderr
from importlib import import_module
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from examples.agent_support import require_model, run_loop
from examples.aws_finops.benchmark import live_benchmark
from examples.aws_finops.mcp_server import run_agent as finops_agent
from examples.online_store.live import sweep
from examples.online_store.mcp_server import run_agent as store_agent
from examples.online_store.planner import plan_question
from examples.sql_math_tracer.agent import run_agent as sales_agent


class ModelConfigurationTests(unittest.IsolatedAsyncioTestCase):
    def test_explicit_model_selection(self):
        self.assertEqual(require_model("  selected-model  "), "selected-model")
        for model in (None, "", " \n", 42):
            with self.subTest(model=model), self.assertRaisesRegex(ValueError, "nonempty model"):
                require_model(model)

    async def test_invalid_model_fails_before_clients_or_output_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "not-created"
            calls = [
                lambda: sales_agent(model=" "),
                lambda: finops_agent(model=" "),
                lambda: store_agent(model=" "),
                lambda: plan_question(None, "A question", model=" "),
                lambda: run_loop(None, None, model=" "),
                lambda: sweep([], output, model=" "),
                lambda: live_benchmark({}, 1, None, {}, None, model=" "),
            ]
            for call in calls:
                with self.subTest(call=call), self.assertRaisesRegex(ValueError, "nonempty model"):
                    await call()
            self.assertFalse(output.exists())

    def test_live_clis_require_model_before_creating_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "not-created"
            commands = [
                ("examples.sql_math_tracer.agent", []),
                ("examples.aws_finops.mcp_server", ["--agent"]),
                ("examples.online_store.mcp_server", ["--agent"]),
                ("examples.aws_finops.benchmark", ["--live", "--output", str(output)]),
                ("examples.online_store.live", ["--output", str(output)]),
            ]
            for module, arguments in commands:
                for selection in ([], ["--model", " "]):
                    with self.subTest(module=module, selection=selection):
                        stderr = StringIO()
                        with patch("sys.argv", [module, *arguments, *selection]), redirect_stderr(stderr):
                            with self.assertRaises(SystemExit) as error:
                                import_module(module).main()
                        self.assertEqual(error.exception.code, 2)
                        self.assertIn("--model", stderr.getvalue())
                        self.assertFalse(output.exists())


class ManifestConfigurationTests(unittest.TestCase):
    def test_manifest_is_required_by_both_python_helpers(self):
        for module, name in (("examples.aws_finops.replay", "replay_artifacts"),
                             ("examples.aws_finops.correction_impact", "correction_impact")):
            with self.subTest(module=module), patch(module + ".Engine") as engine:
                with self.assertRaisesRegex(TypeError, "manifest_path"):
                    getattr(import_module(module), name)([])
                engine.assert_not_called()

    def test_manifest_is_required_by_both_clis(self):
        commands = [
            ("examples.aws_finops.replay", ["saved.json", "--output", "replay-new.json"]),
            ("examples.aws_finops.correction_impact", ["--correction", "min-savings",
                "--artifacts", "saved.json", "--output", "impact-new.json"]),
        ]
        for module, arguments in commands:
            with self.subTest(module=module):
                stderr = StringIO()
                with patch("sys.argv", [module, *arguments]), redirect_stderr(stderr):
                    with self.assertRaises(SystemExit) as error:
                        import_module(module).main()
                self.assertEqual(error.exception.code, 2)
                self.assertIn("--manifest", stderr.getvalue())

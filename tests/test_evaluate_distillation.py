import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import evaluate_distillation as evaluation


class EvaluationScorerTests(unittest.TestCase):
    def row(self, scorer):
        return {"prompt": "test", "scorer": scorer}

    def test_exact_normalizes_case_and_whitespace(self):
        result = evaluation.score_completion(
            self.row({"type": "exact", "expected": "Tokyo"}), "  TOKYO\n"
        )
        self.assertTrue(result["passed"])

    def test_numeric_uses_tolerance(self):
        result = evaluation.score_completion(
            self.row({"type": "numeric", "expected": 3.14, "tolerance": 0.01}),
            "3.145",
        )
        self.assertTrue(result["passed"])

    def test_contains_enforces_forbidden_text(self):
        result = evaluation.score_completion(
            self.row(
                {
                    "type": "contains",
                    "required": "ELIGIBLE",
                    "forbidden": "INELIGIBLE",
                    "case_sensitive": True,
                }
            ),
            "INELIGIBLE",
        )
        self.assertFalse(result["passed"])

    def test_json_can_match_subset(self):
        result = evaluation.score_completion(
            self.row(
                {
                    "type": "json",
                    "expected": {"status": "ok"},
                    "exact": False,
                }
            ),
            json.dumps({"status": "ok", "detail": "accepted"}),
        )
        self.assertTrue(result["passed"])

    def test_rejects_training_prompt_overlap(self):
        with self.assertRaisesRegex(ValueError, "Evaluation leakage"):
            evaluation.reject_training_overlap(
                [{"id": "held-out", "prompt": " Same prompt "}],
                [{"prompt": "same   prompt", "completion": "answer"}],
            )

    def test_report_measures_paired_improvement(self):
        rows = [
            {"id": "a", "prompt": "a", "scorer": {"type": "exact", "expected": "yes"}},
            {"id": "b", "prompt": "b", "scorer": {"type": "exact", "expected": "yes"}},
        ]
        predictions = {
            "base": [
                {"id": "a", "prompt": "a", "completion": "no"},
                {"id": "b", "prompt": "b", "completion": "yes"},
            ],
            "distilled": [
                {"id": "a", "prompt": "a", "completion": "yes"},
                {"id": "b", "prompt": "b", "completion": "yes"},
            ],
        }
        report = evaluation.build_report(rows, predictions)
        comparison = report["comparisons"]["distilled_vs_base"]
        self.assertEqual(comparison["delta"], 0.5)
        self.assertEqual(comparison["wins"], 1)
        self.assertEqual(comparison["regressions"], 0)

    def test_cli_runs_generation_scoring_and_report_chain(self):
        fake_mx = types.ModuleType("mlx.core")
        fake_mx.clear_cache = lambda: None
        fake_mlx = types.ModuleType("mlx")
        fake_mlx.core = fake_mx

        fake_mlx_lm = types.ModuleType("mlx_lm")
        fake_sample_utils = types.ModuleType("mlx_lm.sample_utils")
        fake_sample_utils.make_sampler = lambda temp: (lambda logits: logits)

        class FakeTokenizer:
            chat_template = None

        def fake_load(model_path, adapter_path=None):
            return {"adapter": adapter_path}, FakeTokenizer()

        def fake_generate(model, tokenizer, **kwargs):
            return "yes" if model["adapter"] else "no"

        fake_mlx_lm.load = fake_load
        fake_mlx_lm.generate = fake_generate
        fake_modules = {
            "mlx": fake_mlx,
            "mlx.core": fake_mx,
            "mlx_lm": fake_mlx_lm,
            "mlx_lm.sample_utils": fake_sample_utils,
        }

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            eval_path = tmp_path / "eval.jsonl"
            eval_path.write_text(
                json.dumps(
                    {
                        "id": "case",
                        "prompt": "answer yes",
                        "scorer": {"type": "exact", "expected": "yes"},
                    }
                )
                + "\n"
            )
            output_dir = tmp_path / "results"
            adapter_dir = tmp_path / "adapter"
            adapter_dir.mkdir()
            (adapter_dir / "adapter_config.json").write_text("{}\n")
            (adapter_dir / "adapters.safetensors").touch()
            argv = [
                "evaluate_distillation.py",
                "--eval-data",
                str(eval_path),
                "--student-model",
                "fake-student",
                "--adapter-path",
                str(adapter_dir),
                "--output-dir",
                str(output_dir),
                "--min-delta",
                "1.0",
            ]
            with mock.patch.dict(sys.modules, fake_modules), mock.patch.object(
                sys, "argv", argv
            ):
                evaluation.main()

            report = json.loads((output_dir / "report.json").read_text())
            self.assertEqual(report["models"]["base"]["accuracy"], 0.0)
            self.assertEqual(report["models"]["distilled"]["accuracy"], 1.0)
            self.assertEqual(
                report["comparisons"]["distilled_vs_base"]["delta"], 1.0
            )
            self.assertIn("+100.0%", (output_dir / "report.md").read_text())

    def test_prepares_legacy_adapter_without_copying_weights(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            adapter_dir = tmp_path / "adapter"
            adapter_dir.mkdir()
            weights = adapter_dir / "adapters.safetensors"
            weights.write_bytes(b"weights")
            prepared = Path(
                evaluation.prepare_adapter_for_loading(
                    str(adapter_dir),
                    tmp_path / "results",
                    rank=4,
                    num_layers=12,
                    scale=10.0,
                )
            )
            config = json.loads((prepared / "adapter_config.json").read_text())
            self.assertEqual(config["num_layers"], 12)
            self.assertEqual(config["lora_parameters"]["rank"], 4)
            self.assertTrue((prepared / "adapters.safetensors").is_symlink())
            self.assertEqual(
                (prepared / "adapters.safetensors").resolve(), weights.resolve()
            )


if __name__ == "__main__":
    unittest.main()

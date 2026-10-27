from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import train_with_samples


class TrainingSampleLauncherTests(unittest.TestCase):
    def run_launcher(self, lora, supported_version="99.0", interval="5", extra_modules=None):
        modules = {"mlx_lm": SimpleNamespace(lora=lora), **(extra_modules or {})}
        argv = ["train_with_samples.py", "--sample-output", "/unused/sample.json", "--sample-run-id", "run",
                "--sample-offset", "20", "--train", "--model", "cached-model", "--iters", "10"]
        with patch.dict(sys.modules, modules), patch.object(sys, "argv", argv), \
                patch("train_with_samples.version", return_value=supported_version), \
                patch.dict("os.environ", {"MOONSHINE_SAMPLE_INTERVAL_SECONDS": interval}):
            train_with_samples.main()
            self.assertEqual(sys.argv, ["train_with_samples.py", "--train", "--model", "cached-model", "--iters", "10"])

    def test_unsupported_version_runs_plain_training_without_optional_apis(self):
        plain = Mock()
        self.run_launcher(SimpleNamespace(main=plain))
        plain.assert_called_once_with()

    def test_disabled_capture_runs_plain_training_without_optional_apis(self):
        plain = Mock()
        self.run_launcher(SimpleNamespace(main=plain), supported_version="0.31.3", interval="0")
        plain.assert_called_once_with()

    def test_missing_supported_version_api_falls_back_to_plain_training(self):
        plain = Mock()
        modules = {
            "mlx_lm.tuner.datasets": SimpleNamespace(CacheDataset=object),
            "mlx_lm.tuner.trainer": SimpleNamespace(iterate_batches=lambda: None),
        }
        self.run_launcher(SimpleNamespace(main=plain), supported_version="0.31.3", extra_modules=modules)
        plain.assert_called_once_with()

    def test_training_failure_is_not_retried_or_swallowed_and_hooks_are_restored(self):
        load, train = Mock(), Mock()
        lora = SimpleNamespace(main=Mock(side_effect=RuntimeError("trainer failed")), load_dataset=load, train=train)
        modules = {
            "mlx_lm.tuner.datasets": SimpleNamespace(CacheDataset=object),
            "mlx_lm.tuner.trainer": SimpleNamespace(iterate_batches=lambda: None),
        }
        with self.assertRaisesRegex(RuntimeError, "trainer failed"):
            self.run_launcher(lora, supported_version="0.31.3", extra_modules=modules)
        self.assertIs(lora.load_dataset, load)
        self.assertIs(lora.train, train)
        lora.main.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

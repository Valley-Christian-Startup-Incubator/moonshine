import math
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

try:
	import mlx.core as mx
	import distill_train
except ImportError:
	mx = None
	distill_train = None


@unittest.skipIf(distill_train is None, "distillation unit tests require MLX on Apple Silicon")
class DistillTrainTests(unittest.TestCase):
	def test_encode_example_appends_end_of_sequence_token(self):
		class Tokenizer:
			eos_token_id = 99

			def encode(self, text):
				return [ord(character) for character in text]

		ids, prompt_len = distill_train.encode_example(Tokenizer(), "P", "C")
		self.assertEqual(ids, [ord("P"), ord("C"), 99])
		self.assertEqual(prompt_len, 1)

	def test_loss_mask_excludes_prompt_and_padding_tokens(self):
		mask = distill_train.build_loss_mask(
			batch_size=2,
			seq_len=6,
			lengths=[6, 4],
			prompt_lens=[3, 2],
		)
		self.assertEqual(
			mask.tolist(),
			[
				[0.0, 0.0, 1.0, 1.0, 1.0],
				[0.0, 1.0, 1.0, 0.0, 0.0],
			],
		)

	def test_identical_logits_have_zero_kl_and_finite_loss(self):
		logits = mx.array([[[2.0, 0.0], [0.0, 2.0], [1.0, 1.0]]])
		targets = mx.array([[0, 1, 0]])
		mask = mx.array([[0.0, 1.0, 1.0]])
		total, kl, ce = distill_train.kl_and_ce_loss(
			logits,
			logits,
			targets,
			mask,
			temperature=2.0,
			alpha=0.5,
		)
		mx.eval(total, kl, ce)
		self.assertAlmostEqual(kl.item(), 0.0, places=6)
		self.assertTrue(math.isfinite(total.item()))
		self.assertTrue(math.isfinite(ce.item()))
		self.assertAlmostEqual(total.item(), ce.item() * 0.5, places=6)

	def test_ce_only_loss_has_zero_kl_diagnostic(self):
		logits = mx.array([[[2.0, 0.0], [0.0, 2.0]]])
		targets = mx.array([[0, 1]])
		mask = mx.array([[1.0, 1.0]])
		total, kl, ce = distill_train.ce_only_loss(logits, targets, mask)
		mx.eval(total, kl, ce)
		self.assertAlmostEqual(kl.item(), 0.0, places=6)
		self.assertAlmostEqual(total.item(), ce.item(), places=6)

	def test_latest_checkpoint_uses_numeric_iteration_order(self):
		with tempfile.TemporaryDirectory() as tmp:
			adapter_dir = Path(tmp)
			for name in ["2_adapters.safetensors", "10_adapters.safetensors", "adapters.safetensors"]:
				(adapter_dir / name).touch()
			latest = distill_train.find_latest_checkpoint(adapter_dir)
			self.assertEqual(latest, adapter_dir / "10_adapters.safetensors")
			self.assertEqual(distill_train.checkpoint_iteration(latest), 10)


if __name__ == "__main__":
	unittest.main()

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from training_samples import SampleWriter, sample_interval, sampling_iterator


class Cache:
    def __init__(self):
        self.rows = [(list(range(100)), 0), (list(range(100, 200)), 0)]

    def __len__(self):
        return len(self.rows)

    def itemlen(self, index):
        return len(self.rows[index][0])

    def __getitem__(self, index):
        return self.rows[index]


def batches(dataset, batch_size, max_seq_length, loop=False, seed=None, comm_group=None):
    order = sorted(range(len(dataset)), key=dataset.itemlen)
    while True:
        for index in order:
            yield dataset[index][0][:max_seq_length]
        if not loop:
            return


class TrainingSamplesTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "training_sample.json"
        self.writer = SampleWriter(str(self.path), str(uuid.uuid4()), 5, offset=20)
        self.writer.decode = lambda tokens, **_: " ".join(map(str, tokens))
        self.iterator = sampling_iterator(batches, Cache, self.writer)

    def test_only_consumed_training_batch_is_published_and_training_output_is_unchanged(self):
        source = self.iterator(Cache(), 1, 40, loop=True)
        self.assertEqual(next(source), list(range(40)))
        self.assertFalse(self.path.exists())
        self.assertEqual(next(source), list(range(100, 140)))
        sample = json.loads(self.path.read_text())
        self.assertEqual(sample["text"], " ".join(map(str, range(39))))
        self.assertEqual(sample["tokenCount"], 39)
        self.assertEqual(sample["step"], 21)
        self.assertEqual(sample["runId"], self.writer.run_id)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_validation_and_sorting_do_not_publish_text(self):
        self.assertEqual(list(self.iterator(Cache(), 1, 4, loop=False)), [[0, 1, 2, 3], [100, 101, 102, 103]])
        self.assertFalse(self.path.exists())
        self.assertIsNone(self.writer.pending)

    def test_capture_is_bounded_and_rate_limited(self):
        with patch("training_samples.time.monotonic", return_value=0):
            source = self.iterator(Cache(), 1, 100, loop=True)
            next(source)
            next(source)
            first = self.path.read_text()
            next(source)
            self.assertEqual(self.path.read_text(), first)
            self.assertEqual(json.loads(first)["tokenCount"], 64)
        with patch("training_samples.time.monotonic", return_value=6):
            next(source)
            next(source)
            self.assertEqual(json.loads(self.path.read_text())["step"], 24)

    def test_final_successful_batch_can_be_flushed_without_another_training_step(self):
        source = self.iterator(Cache(), 1, 10, loop=True)
        next(source)
        self.writer.publish_pending()
        self.assertEqual(json.loads(self.path.read_text())["step"], 21)
        self.writer.publish_pending()
        self.assertEqual(json.loads(self.path.read_text())["step"], 21)

    def test_write_failure_disables_capture_without_stopping_training(self):
        source = self.iterator(Cache(), 1, 100, loop=True)
        next(source)
        with patch("training_samples.os.replace", side_effect=OSError("disk unavailable")) as replace:
            self.assertEqual(next(source), list(range(100, 200)))
            self.assertEqual(next(source), list(range(100)))
            self.assertEqual(replace.call_count, 1)
        self.assertFalse(self.writer.enabled)
        self.assertFalse(self.path.exists())
        self.assertEqual(list(self.path.parent.iterdir()), [])

    def test_decoder_failure_disables_only_capture(self):
        self.writer.decode = lambda *args, **kwargs: 1 / 0
        source = self.iterator(Cache(), 1, 100, loop=True)
        next(source)
        self.assertEqual(next(source), list(range(100, 200)))
        self.assertFalse(self.writer.enabled)

    def test_disabled_capture_never_decodes_or_writes(self):
        self.writer.enabled = False
        self.writer.decode = lambda *args, **kwargs: self.fail("decoder should not run")
        source = self.iterator(Cache(), 1, 100, loop=True)
        next(source)
        next(source)
        self.assertFalse(self.path.exists())

    def test_configuration_is_bounded(self):
        for raw in ["0", "-1", "invalid", "nan", "inf"]:
            self.assertEqual(sample_interval(raw), 0)
        self.assertEqual(sample_interval(None), 5)
        self.assertEqual(sample_interval("0.1"), 5)
        self.assertEqual(sample_interval("100000"), 3600)
        self.assertEqual(sample_interval("12.5"), 12.5)


if __name__ == "__main__":
    unittest.main()

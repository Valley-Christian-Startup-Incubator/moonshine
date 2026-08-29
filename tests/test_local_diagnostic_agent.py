import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

try:
	from local_diagnostic_agent import Diagnosis, DiagnosticFiles, restrict_retry_parameters
except ImportError:
	Diagnosis = None
	DiagnosticFiles = None
	restrict_retry_parameters = None


@unittest.skipIf(Diagnosis is None, "local diagnostic tests require pydantic-ai")
class LocalDiagnosticAgentTests(unittest.TestCase):
	def test_retry_parameters_are_limited_to_original_job_keys(self):
		diagnosis = Diagnosis(
			diagnosis="Out of memory.",
			suggested_fix="Use a smaller batch.",
			retry_parameters={"BATCH_SIZE": 2, "UNRECOGNIZED": True},
		)

		result = restrict_retry_parameters(diagnosis, {"BATCH_SIZE"})

		self.assertEqual(result.retry_parameters, {"BATCH_SIZE": 2})

	def test_file_tools_cannot_escape_allowed_directory(self):
		with tempfile.TemporaryDirectory() as tmp:
			root = Path(tmp)
			results = root / "results" / "job-1"
			results.mkdir(parents=True)
			files = DiagnosticFiles(str(root), str(results))

			self.assertIn("escapes", files.read_artifact("../../../etc/passwd"))

	def test_results_directory_must_belong_to_distill_home(self):
		with tempfile.TemporaryDirectory() as tmp:
			root = Path(tmp)
			outside = root / "outside"
			outside.mkdir()

			with self.assertRaisesRegex(ValueError, "escapes"):
				DiagnosticFiles(str(root), str(outside))

	def test_log_tools_return_numbered_bounded_evidence(self):
		with tempfile.TemporaryDirectory() as tmp:
			root = Path(tmp)
			results = root / "results" / "job-1"
			results.mkdir(parents=True)
			(results / "log.txt").write_text("setup\nInsufficient Memory\nfailed\n")
			files = DiagnosticFiles(str(root), str(results))

			self.assertEqual(files.search_log("memory"), "2: Insufficient Memory")
			self.assertEqual(files.read_log(2, 2), "2: Insufficient Memory\n3: failed")


if __name__ == "__main__":
	unittest.main()

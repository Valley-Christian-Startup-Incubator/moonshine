import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import diagnose_job

class DiagnoseJobTests(unittest.TestCase):
	@patch.dict(os.environ, {
		"DIAGNOSTIC_MODEL": "local-test-model",
		"DIAGNOSTIC_OLLAMA_URL": "http://localhost:11434/",
	}, clear=False)
	def test_invoke_ollama_uses_pydantic_agent(self):
		run_local_diagnosis = Mock(return_value="## Diagnosis\nOut of memory.")
		fake_module = types.ModuleType("local_diagnostic_agent")
		fake_module.run_local_diagnosis = run_local_diagnosis

		with patch.dict(sys.modules, {"local_diagnostic_agent": fake_module}):
			result = diagnose_job.invoke_ollama(
				"diagnose this",
				distill_home="/distill",
				results_dir="/distill/results/job-1",
				allowed_retry_keys={"BATCH_SIZE"},
			)

		self.assertEqual(result.returncode, 0)
		self.assertEqual(result.stdout, "## Diagnosis\nOut of memory.")
		run_local_diagnosis.assert_called_once_with(
			"diagnose this",
			distill_home="/distill",
			results_dir="/distill/results/job-1",
			model_name="local-test-model",
			base_url="http://localhost:11434",
			allowed_retry_keys={"BATCH_SIZE"},
		)

	@patch.dict(os.environ, {"DIAGNOSTIC_AGENT": "ollama"}, clear=False)
	def test_preferred_ollama_does_not_require_cli_on_path(self):
		name, invoke = diagnose_job.find_agent()

		self.assertTrue(name.startswith("ollama/"))
		self.assertTrue(callable(invoke))

	def test_extract_retry_params(self):
		output = "## Retry params\n{\"BATCH_SIZE\": 2}\n"
		self.assertEqual(diagnose_job.extract_retry_params(output), {"BATCH_SIZE": 2})

	def test_text_response_prompt_adds_format_only_for_cli_agents(self):
		prompt = diagnose_job.text_response_prompt("Job type: finetune")
		self.assertIn("answer in exactly this format", prompt)
		self.assertIn("Job type: finetune", prompt)


if __name__ == "__main__":
	unittest.main()

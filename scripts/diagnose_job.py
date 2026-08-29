#!/usr/bin/env python3
"""Best-effort AI diagnosis for a failed job.

Invoked by completion_hook.py's --diagnose flag when a DAG's failure
handler fires (i.e. after Dagu's retryPolicy is exhausted). Shells out to
whichever headless coding agent CLI is installed — Claude Code or the
OpenAI Codex CLI — with the job's params and log tail, and asks it to
explain the likely cause and, if the fix looks like a parameter change
(e.g. lower batch size for an OOM), propose adjusted params.

The agent can also be a local Ollama model. Pydantic AI runs that model in
a bounded, read-only tool loop and validates its final answer.

This script never edits files or resubmits jobs itself. It only writes
diagnosis.md (shown on the job detail page) and, if the agent proposed
one, suggested_retry.json (a plain param dict a human can choose to retry
with from the admin/job UI).

Must never fail the DAG run: every path either writes a diagnosis.md (with
the agent's output or a "why we skipped" note) or exits quietly. If even
writing the fallback note fails, we print to stderr and exit 0 regardless.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request

AGENT_TIMEOUT_SEC = 120
LOG_TAIL_LINES = 150
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen3.8:27b-mlx"

TEXT_RESPONSE_INSTRUCTIONS = """You are diagnosing a failed job on a classroom ML job scheduler (Dagu + MLX on Apple Silicon).
Read the job context below and answer in exactly this format:

## Diagnosis
<1-3 sentences: the likely root cause>

## Suggested fix
<1-3 sentences: what a human should change, or "retry as-is" if this looks transient>

## Retry params
<A JSON object with ONLY the parameter keys that should change and their new values, e.g. {{"BATCH_SIZE": 2}}. \
If no parameter change is appropriate (needs a different input file, a code fix, isn't retryable, etc.), output exactly: {{}}>
"""

JOB_CONTEXT_TEMPLATE = """
Job type: {job_type}
Team: {team}
Params: {params}

Log tail (last {log_lines} lines):
```
{log_tail}
```
"""


def text_response_prompt(job_context: str) -> str:
	return f"{TEXT_RESPONSE_INSTRUCTIONS}\n{job_context}"


def invoke_ollama(
	prompt: str,
	*,
	distill_home: str,
	results_dir: str,
	allowed_retry_keys: set[str],
) -> subprocess.CompletedProcess:
	"""Run the diagnostic prompt through the local Pydantic AI agent."""
	base_url = os.environ.get("DIAGNOSTIC_OLLAMA_URL", DEFAULT_OLLAMA_URL).rstrip("/")
	model = os.environ.get("DIAGNOSTIC_MODEL", DEFAULT_OLLAMA_MODEL).strip()
	try:
		from local_diagnostic_agent import run_local_diagnosis

		content = run_local_diagnosis(
			prompt,
			distill_home=distill_home,
			results_dir=results_dir,
			model_name=model,
			base_url=base_url,
			allowed_retry_keys=allowed_retry_keys,
		)
		if not content:
			return subprocess.CompletedProcess(
				["pydantic-ai", model], 1, "", "local agent returned no diagnosis"
			)
		return subprocess.CompletedProcess(["pydantic-ai", model], 0, content, "")
	except Exception as e:
		return subprocess.CompletedProcess(
			["pydantic-ai", model], 1, "", f"{type(e).__name__}: {str(e)[:450]}"
		)


def ollama_is_available() -> bool:
	base_url = os.environ.get("DIAGNOSTIC_OLLAMA_URL", DEFAULT_OLLAMA_URL).rstrip("/")
	try:
		with urllib.request.urlopen(f"{base_url}/api/tags", timeout=1):
			return True
	except (OSError, urllib.error.URLError):
		return False


def find_agent(
	*,
	distill_home: str | None = None,
	results_dir: str | None = None,
	allowed_retry_keys: set[str] | None = None,
):
	"""Returns (agent_name, invoke_fn) for an available diagnostic agent.

	Prefers $DIAGNOSTIC_AGENT when set to "ollama", "claude", or "codex".
	Returns (None, None) if no configured backend is available.

	NOTE: both CLIs' non-interactive flags have shifted across releases.
	Verify `claude -p` / `codex exec` still match the installed version if
	this stops producing output — this is the only function that needs
	updating.
	"""
	preferred = os.environ.get("DIAGNOSTIC_AGENT", "").strip().lower()
	candidates = ["claude", "codex", "ollama"]
	if preferred in candidates:
		candidates = [preferred] + [c for c in candidates if c != preferred]

	for name in candidates:
		if name == "ollama":
			if preferred == "ollama" or ollama_is_available():
				model = os.environ.get("DIAGNOSTIC_MODEL", DEFAULT_OLLAMA_MODEL).strip()
				return f"ollama/{model}", lambda prompt: invoke_ollama(
					prompt,
					distill_home=distill_home or os.path.expanduser("~/.distill"),
					results_dir=results_dir or "",
					allowed_retry_keys=allowed_retry_keys or set(),
				)
			continue
		path = shutil.which(name)
		if not path:
			continue
		if name == "claude":
			return name, lambda prompt: subprocess.run(
				[path, "-p", text_response_prompt(prompt), "--output-format", "text"],
				capture_output=True,
				text=True,
				timeout=AGENT_TIMEOUT_SEC,
			)
		if name == "codex":
			return name, lambda prompt: subprocess.run(
				[path, "exec", "--skip-git-repo-check", text_response_prompt(prompt)],
				capture_output=True,
				text=True,
				timeout=AGENT_TIMEOUT_SEC,
			)
	return None, None


def extract_section(text: str, header: str) -> str:
	pattern = rf"##\s*{re.escape(header)}\s*\n(.*?)(?=\n##|\Z)"
	match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
	return match.group(1).strip() if match else ""


def extract_retry_params(text: str) -> dict:
	section = extract_section(text, "Retry params")
	json_match = re.search(r"\{.*\}", section, re.DOTALL)
	if not json_match:
		return {}
	try:
		parsed = json.loads(json_match.group(0))
		return parsed if isinstance(parsed, dict) else {}
	except json.JSONDecodeError:
		return {}


def write_fallback(results_dir: str, reason: str) -> None:
	try:
		with open(os.path.join(results_dir, "diagnosis.md"), "w") as f:
			f.write(f"_Automated diagnosis unavailable: {reason}._\n")
	except OSError as e:
		print(f"diagnose_job.py: could not write fallback diagnosis: {e}", file=sys.stderr)


def main() -> None:
	parser = argparse.ArgumentParser()
	parser.add_argument("--job-id", required=True)
	args = parser.parse_args()

	distill_home = os.environ.get("DISTILL_HOME", os.path.expanduser("~/.distill"))
	results_dir = os.path.join(distill_home, "results", args.job_id)
	jobs_dir = os.path.join(distill_home, "jobs", args.job_id)
	os.makedirs(results_dir, exist_ok=True)

	try:
		with open(os.path.join(jobs_dir, "meta.json")) as f:
			meta = json.load(f)
	except (OSError, json.JSONDecodeError) as e:
		write_fallback(results_dir, f"could not read job metadata ({e})")
		return

	log_path = os.path.join(results_dir, "log.txt")
	try:
		with open(log_path) as f:
			tail = "".join(f.readlines()[-LOG_TAIL_LINES:])
	except OSError:
		tail = "(no log output captured)"

	params = meta.get("params", {})
	if not isinstance(params, dict):
		params = {}
	agent_name, invoke = find_agent(
		distill_home=distill_home,
		results_dir=results_dir,
		allowed_retry_keys=set(params),
	)
	if invoke is None:
		write_fallback(results_dir, "no Claude, Codex, or Ollama backend available")
		return

	prompt = JOB_CONTEXT_TEMPLATE.format(
		job_type=meta.get("type", "unknown"),
		team=meta.get("team", "unknown"),
		params=json.dumps(params),
		log_lines=LOG_TAIL_LINES,
		log_tail=tail,
	)

	try:
		result = invoke(prompt)
	except subprocess.TimeoutExpired:
		write_fallback(results_dir, f"{agent_name} timed out after {AGENT_TIMEOUT_SEC}s")
		return
	except OSError as e:
		write_fallback(results_dir, f"failed to run {agent_name} ({e})")
		return

	if result.returncode != 0 or not result.stdout.strip():
		write_fallback(
			results_dir,
			f"{agent_name} exited {result.returncode}: {result.stderr.strip()[:500]}",
		)
		return

	output = result.stdout.strip()
	with open(os.path.join(results_dir, "diagnosis.md"), "w") as f:
		f.write(f"_Diagnosed by `{agent_name}` (headless)._\n\n{output}\n")

	retry_params = extract_retry_params(output)
	if retry_params:
		with open(os.path.join(results_dir, "suggested_retry.json"), "w") as f:
			json.dump(retry_params, f, indent=2)


if __name__ == "__main__":
	main()

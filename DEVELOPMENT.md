# Development

Internals for anyone modifying this repo. See [README.md](README.md) for
the user-facing quick start.

## Architecture

```
Browser ──▶ SvelteKit app (port 3000) ──▶ Dagu REST API (port 8081) ──▶ mlx-lm
                                                       │
                                              ~/.distill/{jobs,results}
```

- **SvelteKit app** (`web/`) — the only thing users interact with. Svelte 5
  runes, TypeScript, Tailwind. All Dagu calls happen server-side
  (`web/src/lib/server/dagu.ts`, `web/src/lib/server/jobs.ts`); the browser
  never talks to Dagu directly. No database — job state is derived from
  Dagu's API plus `status.json`/`diagnosis.md`/`suggested_retry.json` files
  written to `~/.distill/results/<job-id>/`.
- **Dagu** (`dagu/`) — a single Go binary that queues and runs jobs. One
  global queue (`mac-studio`, `maxConcurrency: 1`) enforces strict serial
  execution across every job type and every team.
- **Scripts** (`scripts/`) — Python/bash invoked by the DAGs to do the
  actual MLX work and to write job lifecycle files.

Workflow definitions live in `dagu/dags/*.yaml`, one per job type. Each
writes `~/.distill/results/<job-id>/{log.txt,status.json,output...}`.

## Job types — implementation

| Type          | Runs                              | Default timeout | Retries |
|---------------|------------------------------------|------------------|---------|
| `prompt-gen`  | `scripts/prompt_gen.py` (skeleton — customize for your project) | 30 min | 3× / 30s |
| `teacher-gen` | `scripts/batch_generate.py` → `mlx_lm.generate` | 4 hr | 3× / 60s, resumes from last completion |
| `finetune`    | `scripts/run_lora.sh` → `mlx_lm.lora --train` | 12 hr | 2× / 60s, resumes from last checkpoint |
| `quantize`    | `mlx_lm.convert -q`               | 4 hr | 3× / 30s |

Every job's main compute step has a Dagu `retryPolicy`, so a transient
failure (network blip, brief OOM) doesn't require a student to resubmit —
Dagu retries automatically and `status.json` only flips to `failed` once
retries are exhausted. Blind retries would be wasteful for multi-hour jobs,
so `teacher-gen` and `finetune` are resumable: `batch_generate.py` skips
completions already written to `output.jsonl`, and `run_lora.sh` resumes
LoRA training from the latest `*_adapters.safetensors` checkpoint in
`ADAPTER_DIR` instead of restarting at iteration 0. `prompt-gen` and
`quantize` are cheap/idempotent enough that a plain from-scratch retry is
fine.

## AI diagnosis on failure

When a job exhausts its retries, `handlerOn.failure` in that job's DAG
calls `scripts/completion_hook.py --status failed --diagnose`. With
`--diagnose` set, the hook shells out to `scripts/diagnose_job.py`, which:

1. Detects whichever headless coding agent CLI is on `PATH` — `claude`
   (Claude Code, `claude -p`) or `codex` (OpenAI Codex CLI, `codex exec`).
   Preference order is `$DIAGNOSTIC_AGENT` (`claude` or `codex`) if set,
   otherwise whichever is found first. If neither is installed, it writes
   a one-line "diagnosis unavailable" note and exits — this is always a
   soft failure, never something that fails the DAG run itself.
2. Sends the job's params and the last 150 lines of `log.txt` in a fixed
   prompt template, asking for a diagnosis, a suggested fix, and — only if
   a parameter change looks like the fix — a JSON object of params to
   change.
3. Writes `diagnosis.md` (shown on the job's detail page) and, if the
   agent proposed param changes, `suggested_retry.json`.

The agent **never edits files or resubmits jobs itself**. A human clicks
"Retry with suggested params" on the job page, which calls
`retryJobWithParams()` (`web/src/lib/server/jobs.ts`) — that resubmits the
same input file under a new job id with the suggested params merged over
the original ones. This is a deliberate scope limit: an unsupervised agent
making file edits on a machine multiple teams share has a much bigger
blast radius than one that only reads logs and proposes numbers.

`AGENT_TIMEOUT_SEC` in `diagnose_job.py` (120s) bounds how long the failure
handler blocks; `completion_hook.py`'s own subprocess timeout (150s) is a
generous outer bound on top of that.

## Python environment

`setup.sh` provisions Python via [uv](https://astral.sh/uv) rather than
depending on whatever `python3` happens to be on the system: it installs
uv itself if missing, then `uv python install ${PYTHON_VERSION}` (default
`3.12`) and `uv venv --python ${PYTHON_VERSION}`. This means a fresh Mac
Studio doesn't need Python preinstalled at a compatible version. If uv
can't be installed (no network, sandboxed environment), it falls back to
system `python3` and hard-fails if that's older than 3.11.

## launchd / PATH notes

launchd's default `PATH` for a `LaunchAgent` is just
`/usr/bin:/bin:/usr/sbin:/sbin` — it does not include Homebrew, `uv`, or
npm-global install locations. Dagu's own steps use absolute paths (the venv
python, `dagu` itself) so this mostly doesn't matter, except for the
diagnosis step, which shells out to `claude`/`codex` **by name**. `setup.sh`
sets an explicit `PATH` in `com.distill.dagu.plist`'s
`EnvironmentVariables` covering `/opt/homebrew/bin`, `/usr/local/bin`,
`~/.local/bin`, `~/.cargo/bin`, and `~/.npm-global/bin` — if you install
`claude`/`codex` somewhere else, add that path there too (or set
`DIAGNOSTIC_AGENT` to a full path via a small wrapper script on one of the
covered directories).

`start.sh` (the manual, non-launchd path) `set -a`-exports everything from
`~/.distill/env` before sourcing it, so `dagu start-all` — which is spawned
without inline `VAR=val` prefixes — still inherits `DAGU_USER`,
`DAGU_PASSWORD`, and `DIAGNOSTIC_AGENT`.

## Known version-drift risks

Several things in this repo pin to a specific external API/CLI surface and
may need adjustment if you're on a different release:

- `web/src/lib/server/dagu.ts` — targets Dagu's `v1` REST API. If job
  submission/cancellation fails after `setup.sh` installs a newer Dagu,
  check that release's REST API docs and adjust the paths in that one file.
- `scripts/batch_generate.py` — targets `mlx_lm.generate()`'s current
  Python API. If `mlx-lm` changes its `temp`/sampler argument shape, this
  is the only file that needs updating.
- `scripts/run_lora.sh` — assumes `mlx_lm.lora` supports `--resume-adapter-file`
  and `--save-every`, and that checkpoints are named `<iter>_adapters.safetensors`.
  Verify these flags against the installed `mlx-lm` version; if they've
  changed, the script just needs its checkpoint glob and flag names updated.
- `scripts/diagnose_job.py`'s `find_agent_cli()` — assumes `claude -p
  --output-format text` and `codex exec` are still the right non-interactive
  invocations for the installed CLI versions. Both tools' headless flags
  have changed across releases; this function is the only place to update.

`DAGU_VERSION` and `PYTHON_VERSION` in `setup.sh` are pinned explicitly —
bump them deliberately rather than tracking `latest`.

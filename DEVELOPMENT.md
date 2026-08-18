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
| `distill`     | `scripts/run_distill.sh` → `scripts/distill_train.py` (custom KL training loop) | 12 hr | 2× / 60s, resumes from last checkpoint |
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

## Response-based vs logit-based training

`finetune` and `distill` both take a `teacher-gen` job's `output.jsonl`
(`{"prompt","completion"}` rows) and produce a LoRA adapter, but they
train differently:

- **`finetune`** (response-based) is plain SFT: `mlx_lm.lora --train`
  does standard next-token cross-entropy against the teacher's generated
  text. The student only ever sees the teacher's single sampled completion
  per prompt.
- **`distill`** (logit-based) trains the student to match the teacher's
  full output *distribution* at every token position, not just the token
  it happened to sample. `scripts/distill_train.py` hand-rolls the
  training loop because `mlx_lm.lora` has no support for a KL-divergence
  loss against a second model:
  1. Load both models; the teacher is frozen, only the student's
     LoRA-injected params (via `mlx_lm.tuner.utils.linear_to_lora_layers`)
     are trainable.
  2. Each batch, run a teacher forward pass (no grad) and a student
     forward pass over the same token ids.
  3. Loss = `ALPHA * KL(teacher_T ‖ student_T) * T² + (1 - ALPHA) * CE`,
     computed only over completion-token positions (prompt tokens and
     padding are masked out). `T` is `TEMPERATURE`; the `T²` factor is the
     standard Hinton et al. correction so the KL term's gradient doesn't
     shrink as temperature grows.
  4. `optim.Adam` updates the student's LoRA params; checkpoints save to
     `<iter>_adapters.safetensors` in `ADAPTER_DIR` every `SAVE_EVERY`
     iterations, same convention as `run_lora.sh`, so `run_distill.sh` can
     resume from the latest one on a Dagu retry.

  We don't precompute/store the teacher's logits — full vocab (~128k
  floats/token for a Llama-family model) over a whole training set is a
  lot of disk for little benefit versus just re-running the teacher
  forward pass each step, which is what mainstream distillation trainers
  do at this scale.

  **Constraint**: the teacher and student must share a tokenizer/vocabulary
  (same model family) since logits are compared position-for-position over
  identical token ids. `distill_train.py` only checks vocab *size* as a
  sanity check — two same-sized but different vocabularies will silently
  misalign and train garbage.

  ⚠️ **This has not been run on real hardware.** MLX is Apple-Silicon-only,
  so it couldn't be tested in the environment it was written in. The
  training math and control flow follow the standard mlx-examples LoRA
  pattern, but expect a debugging pass on the actual Mac Studio the first
  time `distill` runs — see the version-drift entry below for the specific
  APIs most likely to need adjustment.

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
- `scripts/distill_train.py` — **highest risk in the repo**, not just
  version drift: it's untested hand-written MLX training code (see above).
  Specifically likely to need fixing: `linear_to_lora_layers`'s config dict
  shape (`rank`/`alpha`/`dropout`/`scale` keys — `mlx_lm.tuner` has changed
  its LoRA config schema across releases), whether `student.unfreeze(keys=[...])`
  is still how to re-enable grad on injected LoRA params after `freeze()`,
  and whether `mlx_lm.load()`'s returned model is still callable directly
  as `model(input_ids) -> logits` for both teacher and student architectures.

`DAGU_VERSION` and `PYTHON_VERSION` in `setup.sh` are pinned explicitly —
bump them deliberately rather than tracking `latest`.

# Distillation Job Scheduler

A classroom tool for running MLX distillation/fine-tuning jobs on a shared
Mac Studio. Student teams submit jobs through a web UI; jobs execute one at
a time (strict FIFO), so everyone shares the machine fairly.

## For students: use the dashboard

You do not need the command line or access to the Mac Studio. Open the web
address your instructor gives you, enter the shared password, then:

1. Open **Lab**. Choose **Quick tour** for highlighted controls, or an info icon for an explanation.
2. **Generate:** start from topics or upload your own questions for teacher answers.
   Browse samples before submitting, and download completed results from **Jobs**.
3. **Split:** upload the teacher Q&A in your browser. Choose 80/20, 85/15, or
   90/10 training/validation and freeze the split. Download both files and the
   split record before leaving the page.
4. **Train:** choose Qwen 4B or 8B and upload only the training file. Model
   selection is visible; additional training settings are collapsed.
5. **Evaluate:** use the same saved validation questions for every comparison.
   This stage explains the intended metrics; it does not run an evaluation yet.

Jobs run one at a time. A **queued** job is waiting for the shared Mac Studio;
a **running** job is being processed. Open any job to see its place in line,
progress, result, or failure explanation.

The usual workflow is:

```text
Generate teacher Q&A → Split → Train student → Evaluate on validation
```

**Train from answers** teaches the student to predict the teacher's saved text.
**Distill token scores** also teaches it from the teacher's next-token
probabilities and requires matching tokenizers. **Reduce model size** exposes
bits per weight: compare each bit depth on the same validation file.

Browser review and splitting accept files up to 20 MB. Splitting uses fixed seed
42 and groups repeated questions together after normalizing case and whitespace,
so those questions cannot cross the split boundary. The split stays locked while
the page is open; the downloaded files are the record to reuse across sessions.
This does not enforce dataset separation in the existing training backend.

The web submission form accepts only configured local Qwen paths, with no hub
fallback. In **Generate**, the teacher panel shows its source status. Choose
**Teacher answers** to set maximum answer length and answer variety. Each info
icon explains the control and how to use it.

To set up the teacher, an instructor opens **Configure teacher**, signs in, and
enters the full path to the installed Qwen MLX folder on the computer running
Moonshine. **Check & save teacher** checks the Qwen configuration, tokenizer,
and weight files, then saves only the folder reference in
`$DISTILL_HOME/teacher-model.json` (normally `~/.distill/teacher-model.json`).
It does not copy weights, download models, run inference, or verify the parameter
count. The instructor must select the approved model. Teacher labels follow the selected
folder name (or the model name for a Hugging Face cache snapshot).

Saved teacher settings take precedence over `MOONSHINE_QWEN_30B_PATH` in the web
process environment. In **Train**, choose **Configure student models** to check and save each installed
Qwen 4B or 8B MLX folder, using the same instructor sign-in. Each student source
is saved separately under `$DISTILL_HOME` and applies to new jobs immediately.
Saved student settings take precedence over `MOONSHINE_QWEN_4B_PATH` and
`MOONSHINE_QWEN_8B_PATH`; those variables remain available as fallbacks.
The checks verify model files, not parameter count or inference. An unconfigured model
cannot be submitted. Ollama model blobs are not MLX training directories.

Training and quantization still run on the shared Studio and store weights
there. Student-machine training and weight transfer are not implemented by this
UI update. The evaluation page describes top-1 next-token agreement (higher is
better) and perplexity on teacher answer tokens (lower is better); the existing
evaluation script below scores answers instead of these token metrics.

JSONL means one JSON object per line. The dashboard defines the terms it uses
and provides a downloadable example for every job that needs a file. For
example, a prompt file looks like this:

```json
{"prompt":"Explain how a gear ratio changes torque."}
```

## Install on the Mac Studio (operator)

```bash
git clone https://github.com/Valley-Christian-Startup-Incubator/moonshine.git
cd moonshine
./setup.sh
```

`setup.sh` installs everything needed (Python, Dagu, the web app) and
registers it to start automatically on boot. It targets Apple Silicon Macs
and never needs `sudo` — every
file it writes lives under your home directory (`~/.local/bin` for the Dagu
binary, `~/.distill` for the app and its data, `~/Library/LaunchAgents` for
the boot services). It prints the web UI URL, shared web password,
admin password, and operator credentials at the end — **save that output**,
it's the only place the generated passwords are shown in full (they're also
saved to `~/.distill/env`, readable only by your user account).

Re-run `./setup.sh` any time to update the stack in place; it's safe to run
repeatedly and preserves the existing ports and passwords. To select values
on the first run (or deliberately change them later), pass environment
variables explicitly:

```bash
WEB_PORT=3001 \
WEB_PASSWORD='shared classroom password' \
ADMIN_PASSWORD='operator password' \
./setup.sh
```

Port `3000` is the default. If another service already uses it, setup stops
with a clear error so you can re-run with `WEB_PORT=<free-port>`. Dagu uses
port `8081` by default and can similarly be changed with `DAGU_PORT`.

## Admin and operator access

- **Admins**: open `http://<mac-studio-ip>:3000/admin` to cancel jobs and check
  whether the scheduler is healthy and how much disk space is left.
  The admin password is generated by `setup.sh` (or set `ADMIN_PASSWORD`
  before running it if you want a specific one).
- **Operators**: the Dagu URL printed by setup opens the workflow-engine
  dashboard. It shows every run, retry, and log line when something needs
  deeper debugging than the job page shows.

## Manual service control

```bash
~/.distill/start.sh   # start everything now, without touching boot config
~/.distill/stop.sh    # stop everything
launchctl list | grep com.distill   # check whether the boot services are running
```

## Automated distillation evaluation

Keep a private evaluation JSONL outside the training set. Each row supplies
an objective scorer:

```json
{"id":"case-1","prompt":"Reply with only the invoice total.","scorer":{"type":"numeric","expected":142.50,"tolerance":0.01}}
{"id":"case-2","prompt":"Return the decision as JSON.","scorer":{"type":"json","expected":{"approved":true}}}
```

Run the same cases against the base student, its adapter, and the teacher:

```bash
~/.distill-venv/bin/python scripts/evaluate_distillation.py \
  --eval-data private-eval.jsonl \
  --training-data teacher-output.jsonl \
  --student-model mlx-community/Meta-Llama-3.1-8B-Instruct-4bit \
  --adapter-path ~/.distill/results/JOB_ID/adapters \
  --teacher-model mlx-community/Meta-Llama-3.1-8B-Instruct \
  --output-dir eval-results
```

The command uses greedy generation, rejects exact prompt leakage, resumes
interrupted generation, and writes `report.md` plus machine-readable
`report.json`. Supported scorer types are `exact`, `contains`, `regex`,
`numeric`, and `json`. Add `--min-delta 0.05` to use a five-point improvement
over the base student as a CI or script gate. Older distillation adapters that
lack `adapter_config.json` use the trainer defaults. Pass `--adapter-rank`,
`--adapter-layers`, and `--adapter-scale` if that job used non-default values.

Always run a matched `ALPHA=0` control. Moonshine skips the teacher entirely
for that control, so it runs faster and measures whether the teacher's logits
help beyond supervised training on the same completions. The audited
[music-theory experiment](experiments/music_theory_reasoning/RESULT.md) found
that a Qwen 35B teacher's logits reduced held-out fact coverage from 94.0% to
53.8%, even though both trained students beat the 17.5% base model.

## Job types

| Type          | What it does                                   | Typical duration |
|---------------|-------------------------------------------------|-------------------|
| `prompt-gen`  | Generates candidate prompts from a config file  | minutes |
| `teacher-gen` | Runs prompts through a teacher model for completions | up to a few hours |
| `finetune`    | Response-based: LoRA fine-tunes a student on the teacher's generated text | up to half a day |
| `distill`     | Logit-based: LoRA trains a student to match the teacher's output distribution, not just its text | up to half a day |
| `quantize`    | Shrinks a model for faster local inference      | up to a few hours |

`finetune` and `distill` both consume a `teacher-gen` job's output and
produce a LoRA adapter — pick whichever matches how your team wants to
train. `distill` needs the teacher and student to be the same model
family (shared tokenizer).

Jobs that fail from a transient hiccup (a brief network blip, a memory
spike) are retried automatically — you usually don't need to do anything.
If a job still fails after retries, the job page shows an AI-generated
diagnosis of what likely went wrong. That diagnosis can use Claude Code,
Codex, or a local model served by Ollama. Local models run through a bounded,
read-only Pydantic AI agent and return schema-validated retry parameters.

## What's intentionally not here

No individual user accounts or per-team permissions, no email notifications,
no CLI client, and no automatic data retention/cleanup. The web UI uses one
shared password; the admin and Dagu operator surfaces have separate credentials.

## For contributors

Architecture, the Dagu/MLX internals, the retry/resume design, and the AI
diagnosis feature are documented in [DEVELOPMENT.md](DEVELOPMENT.md).

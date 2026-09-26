# Moonshine redesign: phase 1 backend

Phase 1 (Sept 25, 2026) adds the new backend. Nothing here is visible on the live site until phase 2 rebuilds the UI.

## Processes
- `com.distill.queue` (launchd, `launchd/com.distill.queue.plist`) runs `~/.distill/scripts/queue_runner.py`.
  It runs one job at a time, first in first out. While a job is paused, the whole queue waits. The runner is the only writer of `results/<id>/status.json`.
- Dagu is no longer used for new jobs. The runner also waits while Dagu is running a legacy job.

## Files (under ~/.distill)
- `jobs/<id>/meta.json`: `{version: 2, id, type: generate|train, ownerName, params, inputFile, inputName, submittedAt}`
- `results/<id>/status.json`: status is one of queued, running, paused, completed, failed, cancelled.
- `results/<id>/progress.json`: phase, done/total, rate, `eta_sec`, and `eta_total_sec` (train jobs).
- `results/<id>/log.txt`, `exit.json`, `diagnosis.md` / `suggested_retry.json` (AI diagnosis after a failure).
- Generate: `results/<id>/output.jsonl`
- Train: `data/{train,valid}.jsonl`, `adapters/` (+ `checkpoints/`), `eval/*.jsonl` (per-item results), `eval_report.{json,md}`, `fused-model/` (4-bit MLX), `state.json`.
- `queue/control/<id>.json`: `{action: pause|resume|cancel|purge}`, written by the web app and consumed by the runner.
- `users.json`: `[{name, createdAt}]`
- `ollama-models.json`: tags Moonshine registered, `[{tag, owner, jobId, student, createdAt}]`
- `models/students/Qwen3-{0.6B,1.7B,4B,8B}-4bit`: APFS clones of `/Users/aimac/Documents/mlx_models/*`. launchd jobs cannot read ~/Documents because of macOS privacy protection.

## Scripts (repo `scripts/`, deployed to `~/.distill/scripts/`)
- `moonshine_common.py`: paths, teacher selection (largest Ollama model, `TEACHER_MODEL` override), Ollama chat, progress/ETA, safe-stop handling.
- `gen_questions.py`: Generate Questions. It sends batches (default 20, `GEN_BATCH_SIZE`) to the teacher, validates, dedupes, and can resume.
- `train_eval.py`: split 80/20 (seed 42, duplicate prompts kept on the same side), `mlx_lm.lora --data <dir>` with segment-aware checkpoint resume, loss, answers, teacher grading, report, fuse, and Ollama register.
- `queue_runner.py`: the queue runner.
- `moonshine_ctl.py`: CLI for the same protocol: submit-generate, submit-train, pause, resume, cancel, purge, list, users.

## Ollama import of a fused student
`mlx_lm.fuse --dequantize` writes bf16 safetensors. Then run `ollama create --experimental -q int4 <tag>` with a Modelfile that contains `FROM .`, `RENDERER qwen3.5`, and `PARSER qwen3.5`.
Two other routes do not work with Ollama 0.32.15:
- The normal GGUF converter rejects `Qwen3ForCausalLM`.
- Importing the 4-bit MLX folder directly produces a broken model.
The tag is `<qwen3-0.6b|qwen3-1.7b|qwen3-4b|qwen3-8b>-<name-slug>-<YYYYMMDD>`, with `-2`, `-3`, and so on added if the tag already exists.

## Server functions for the UI (web/src/lib/server)
- `students.ts`: `STUDENTS`, `listStudents()`, `isStudentId()`
- `ollama.ts`: `getTeacherModel()`, `teacherChat()`, `listOllamaModels()`, `listRegisteredModels(owner?)`, `deleteRegisteredModel(tag)`
- `interview.ts`: `INTERVIEW_META_PROMPT`, `runInterviewTurn(messages)`, `extractPrompt()`, `getMetaPromptDownload(fmt)`, `getPromptDownload(prompt, fmt, owner)`
- `queue.ts`: `submitGenerateJob()`, `submitTrainJob()`, `countValidPairs()`, `listJobs({ownerName?})`, `getJob()`, `getQueueSummary()`,
  `pauseJob()`, `resumeJob()`, `cancelJob()`, `purgeJob()`, `retryJob(id, owner, overrides?)`, `getJobLogTail()`, `getJobDiagnosis()`,
  `getEvalReport()`, `getTrainingCurve()`, `listJobArtifacts()` (use with `downloads.validateResultPath(jobId, relPath)`), `formatEta()`
- `users.ts`: `USER_COOKIE`, `listUsers()`, `addUser()`, `findUser()`, `getCurrentUserName(cookies)`, `selectUser()`, `clearSelectedUser()`,
  `previewDeleteUser(name)`, `deleteUser(name)` (admin)
- `hooks.server.ts` sets `locals.userName`.

## Endpoints
- `POST /api/interview` `{messages}` returns `{reply, prompt|null}`
- `GET /api/meta-prompt?format=md|txt`
- `POST /api/prompt-download` `{prompt, format}`
- `GET /api/jobs[?mine=1]` returns `{jobs, queue}`
- `POST /api/jobs/:id/{pause|resume|cancel|retry}`
- `GET|POST /api/users`, `POST /api/users/select`
- `GET|POST /admin/delete-user` (requires the admin cookie from the existing `/admin` login action)

#!/usr/bin/env bash
# Wrapper around scripts/distill_train.py that resumes from the latest
# checkpoint if one exists in ADAPTER_DIR — same rationale as run_lora.sh:
# distill.yaml gives this step a Dagu retryPolicy, and a logit-distillation
# run can take hours, so a retry should continue, not restart at iteration 0.
#
# Usage: run_distill.sh TEACHER_MODEL STUDENT_MODEL INPUT_FILE ADAPTER_DIR \
#          ITERS BATCH_SIZE LEARNING_RATE SAVE_EVERY TEMPERATURE ALPHA LORA_RANK LORA_LAYERS

set -euo pipefail

TEACHER_MODEL="$1"
STUDENT_MODEL="$2"
INPUT_FILE="$3"
ADAPTER_DIR="$4"
ITERS="$5"
BATCH_SIZE="$6"
LEARNING_RATE="$7"
SAVE_EVERY="$8"
TEMPERATURE="$9"
ALPHA="${10}"
LORA_RANK="${11}"
LORA_LAYERS="${12}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RESUME_ARGS=()
latest_checkpoint="$(ls -1 "${ADAPTER_DIR}"/*_adapters.safetensors 2>/dev/null | sort -t/ -k1 -V | tail -1 || true)"
if [[ -n "${latest_checkpoint}" ]]; then
	echo "Resuming distillation from checkpoint: ${latest_checkpoint}"
	RESUME_ARGS=(--resume-adapter-file "${latest_checkpoint}")
fi

exec python3 "${SCRIPT_DIR}/distill_train.py" \
	--teacher-model "${TEACHER_MODEL}" \
	--student-model "${STUDENT_MODEL}" \
	--data "${INPUT_FILE}" \
	--adapter-path "${ADAPTER_DIR}" \
	--iters "${ITERS}" \
	--batch-size "${BATCH_SIZE}" \
	--learning-rate "${LEARNING_RATE}" \
	--save-every "${SAVE_EVERY}" \
	--temperature "${TEMPERATURE}" \
	--alpha "${ALPHA}" \
	--lora-rank "${LORA_RANK}" \
	--lora-layers "${LORA_LAYERS}" \
	"${RESUME_ARGS[@]}"

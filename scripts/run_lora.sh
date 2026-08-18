#!/usr/bin/env bash
# Wrapper around mlx_lm.lora that resumes from the latest checkpoint if one
# exists in ADAPTER_DIR. finetune.yaml gives the lora-train step a Dagu
# retryPolicy; on a retry this script re-runs, finds the checkpoint saved by
# the previous (failed) attempt, and continues training instead of starting
# a 12-hour job over from iteration 0.
#
# Usage: run_lora.sh MODEL_PATH INPUT_FILE ADAPTER_DIR ITERS BATCH_SIZE LEARNING_RATE SAVE_EVERY

set -euo pipefail

MODEL_PATH="$1"
INPUT_FILE="$2"
ADAPTER_DIR="$3"
ITERS="$4"
BATCH_SIZE="$5"
LEARNING_RATE="$6"
SAVE_EVERY="$7"

RESUME_ARGS=()
latest_checkpoint="$(ls -1 "${ADAPTER_DIR}"/*_adapters.safetensors 2>/dev/null | sort -t/ -k1 -V | tail -1 || true)"
if [[ -n "${latest_checkpoint}" ]]; then
	echo "Resuming LoRA training from checkpoint: ${latest_checkpoint}"
	RESUME_ARGS=(--resume-adapter-file "${latest_checkpoint}")
fi

exec mlx_lm.lora \
	--model "${MODEL_PATH}" \
	--train \
	--data "${INPUT_FILE}" \
	--adapter-path "${ADAPTER_DIR}" \
	--iters "${ITERS}" \
	--batch-size "${BATCH_SIZE}" \
	--learning-rate "${LEARNING_RATE}" \
	--save-every "${SAVE_EVERY}" \
	"${RESUME_ARGS[@]}"

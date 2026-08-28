#!/usr/bin/env python3
"""Logit-based (KL) distillation training loop.

Complements finetune.yaml's response-based SFT (train the student on the
teacher's *generated text* with next-token cross-entropy). This script
instead matches the student's output *distribution* to the teacher's at
every token position: the teacher does a forward pass alongside the
student on each training batch, and the student's LoRA params are updated
to minimize KL divergence from the teacher's temperature-softened
distribution, blended with a standard hard-label cross-entropy term
(`loss = ALPHA * KL + (1 - ALPHA) * CE`).

We don't precompute/store teacher logits: full-vocab logits (~128k floats
per token for a Llama-family model) over an entire training set is a lot
of disk for little benefit versus just re-running the teacher forward pass
each step, which is what mainstream distillation trainers do at this
scale.

Requires the teacher and student to share a tokenizer/vocabulary (same
model family, e.g. both Llama-3.1-8B derivatives) since logits are
compared position-for-position over the same token ids. This script only
checks vocab *size* as a sanity check — it cannot detect two same-sized
but different vocabularies, so mismatched tokenizers will silently
misalign and produce garbage.

Input JSONL row shape (matches teacher-gen.yaml's output.jsonl):
    {"prompt": "...", "completion": "..."}

Verified on Apple Silicon with MLX 0.32.1 and mlx-lm 0.31.3 using a
Qwen2.5 1.5B/0.5B teacher-student pair. Direct training, finite KL/CE
losses, checkpoint creation, interrupted-run resume, and the installed
Dagu workflow have all completed successfully.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import mlx.core as mx
import mlx.nn as nn
import mlx.optimizers as optim
from mlx.utils import tree_flatten
from mlx_lm import load
from mlx_lm.tuner.utils import linear_to_lora_layers


def read_rows(path: str) -> list[dict]:
	with open(path) as f:
		return [json.loads(line) for line in f if line.strip()]


def format_prompt(tokenizer, prompt: str) -> str:
	if tokenizer.chat_template is not None:
		messages = [{"role": "user", "content": prompt}]
		return tokenizer.apply_chat_template(messages, add_generation_prompt=True, tokenize=False)
	return prompt


def encode_example(tokenizer, prompt_text: str, completion_text: str) -> tuple[list[int], int]:
	prompt_ids = tokenizer.encode(prompt_text)
	full_ids = tokenizer.encode(prompt_text + completion_text)
	eos_id = tokenizer.eos_token_id
	if eos_id is not None and (not full_ids or full_ids[-1] != eos_id):
		full_ids.append(eos_id)
	# If the tokenizer isn't perfectly prefix-stable across the concatenation
	# boundary, this can be off by a token or two right at the boundary —
	# acceptable label noise for LoRA-scale training, not worth a slower
	# token-level diff here.
	return full_ids, len(prompt_ids)


def build_examples(rows: list[dict], tokenizer) -> list[tuple[list[int], int]]:
	examples = []
	for row in rows:
		prompt_text = format_prompt(tokenizer, row["prompt"])
		ids, prompt_len = encode_example(tokenizer, prompt_text, row["completion"])
		if len(ids) >= 2 and prompt_len < len(ids):
			examples.append((ids, prompt_len))
	if not examples:
		raise ValueError("No usable training examples in input file")
	return examples


def batch_iterator(examples: list[tuple[list[int], int]], batch_size: int):
	i = 0
	n = len(examples)
	while True:
		batch = [examples[(i + j) % n] for j in range(batch_size)]
		i = (i + batch_size) % n
		yield batch


def pad_batch(batch: list[tuple[list[int], int]], pad_id: int):
	max_len = max(len(ids) for ids, _ in batch)
	input_ids = mx.array([ids + [pad_id] * (max_len - len(ids)) for ids, _ in batch])
	lengths = [len(ids) for ids, _ in batch]
	prompt_lens = [p for _, p in batch]
	return input_ids, lengths, prompt_lens


def build_loss_mask(batch_size: int, seq_len: int, lengths: list[int], prompt_lens: list[int]):
	# Loss (both KL and CE) is only computed on completion tokens, predicting
	# position t+1 from position t — padding and prompt tokens are masked out.
	mask = [[0.0] * (seq_len - 1) for _ in range(batch_size)]
	for b in range(batch_size):
		start = max(prompt_lens[b] - 1, 0)
		end = lengths[b] - 1
		for t in range(start, end):
			mask[b][t] = 1.0
	return mx.array(mask)


def kl_and_ce_loss(student_logits, teacher_logits, targets, mask, temperature: float, alpha: float):
	t = temperature
	student_log_probs_t = student_logits / t - mx.logsumexp(student_logits / t, axis=-1, keepdims=True)
	teacher_log_probs_t = teacher_logits / t - mx.logsumexp(teacher_logits / t, axis=-1, keepdims=True)
	teacher_probs_t = mx.exp(teacher_log_probs_t)

	kl_per_token = mx.sum(teacher_probs_t * (teacher_log_probs_t - student_log_probs_t), axis=-1)
	denom = mx.maximum(mask.sum(), 1)
	kl_loss = (kl_per_token * mask).sum() / denom
	# Standard T^2 scaling (Hinton et al.) so the KL term's gradient magnitude
	# doesn't shrink as temperature grows.
	kl_loss = kl_loss * (t**2)

	ce_per_token = nn.losses.cross_entropy(student_logits, targets, reduction="none")
	ce_loss = (ce_per_token * mask).sum() / denom

	total = alpha * kl_loss + (1 - alpha) * ce_loss
	return total, kl_loss, ce_loss


def find_latest_checkpoint(adapter_dir: Path) -> Path | None:
	checkpoints = sorted(
		adapter_dir.glob("*_adapters.safetensors"),
		key=checkpoint_iteration,
	)
	return checkpoints[-1] if checkpoints else None


def checkpoint_iteration(path: Path) -> int:
	prefix = path.name.split("_", 1)[0]
	return int(prefix) if prefix.isdigit() else 0


def main() -> None:
	parser = argparse.ArgumentParser()
	parser.add_argument("--teacher-model", required=True)
	parser.add_argument("--student-model", required=True)
	parser.add_argument("--data", required=True)
	parser.add_argument("--adapter-path", required=True)
	parser.add_argument("--iters", type=int, default=1000)
	parser.add_argument("--batch-size", type=int, default=4)
	parser.add_argument("--learning-rate", type=float, default=1e-5)
	parser.add_argument("--save-every", type=int, default=100)
	parser.add_argument("--temperature", type=float, default=2.0)
	parser.add_argument("--alpha", type=float, default=0.5, help="weight of KL loss vs hard-label CE")
	parser.add_argument("--lora-rank", type=int, default=8)
	parser.add_argument("--lora-layers", type=int, default=16)
	parser.add_argument("--resume-adapter-file", default=None)
	args = parser.parse_args()

	adapter_dir = Path(args.adapter_path)
	adapter_dir.mkdir(parents=True, exist_ok=True)
	adapter_config = {
		"fine_tune_type": "lora",
		"num_layers": args.lora_layers,
		"lora_parameters": {
			"rank": args.lora_rank,
			"dropout": 0.0,
			"scale": 20.0,
		},
	}
	with open(adapter_dir / "adapter_config.json", "w") as f:
		json.dump(adapter_config, f, indent=2)
		f.write("\n")

	print(f"Loading teacher: {args.teacher_model}", file=sys.stderr)
	teacher, tokenizer = load(args.teacher_model)
	teacher.freeze()

	print(f"Loading student: {args.student_model}", file=sys.stderr)
	student, student_tokenizer = load(args.student_model)

	if student_tokenizer.vocab_size != tokenizer.vocab_size:
		raise ValueError(
			f"Teacher/student vocab size mismatch ({tokenizer.vocab_size} vs "
			f"{student_tokenizer.vocab_size}) — logit distillation requires a "
			"shared tokenizer. Use models from the same family."
		)

	lora_config = {"rank": args.lora_rank, "dropout": 0.0, "scale": 20.0}
	linear_to_lora_layers(student, args.lora_layers, lora_config)
	student.freeze()
	# linear_to_lora_layers unfreezes the LoRA-injected params it adds; the
	# freeze() above is for the (much larger) frozen base weights.
	for _, module in student.named_modules():
		if hasattr(module, "lora_a"):
			module.unfreeze(keys=["lora_a", "lora_b"])

	resume_path = args.resume_adapter_file or find_latest_checkpoint(adapter_dir)
	completed_iterations = 0
	if resume_path and Path(resume_path).exists():
		print(f"Resuming from checkpoint: {resume_path}", file=sys.stderr)
		student.load_weights(str(resume_path), strict=False)
		completed_iterations = checkpoint_iteration(Path(resume_path))

	if completed_iterations >= args.iters:
		print(
			f"Checkpoint already completed {completed_iterations} iterations "
			f"(requested {args.iters}); nothing to do",
			file=sys.stderr,
		)
		print("Training complete", file=sys.stderr)
		return

	rows = read_rows(args.data)
	examples = build_examples(rows, tokenizer)
	print(
		f"Training on {len(examples)} examples from iteration "
		f"{completed_iterations + 1} through {args.iters}",
		file=sys.stderr,
	)
	batches = batch_iterator(examples, args.batch_size)
	for _ in range(completed_iterations):
		next(batches)

	optimizer = optim.Adam(learning_rate=args.learning_rate)
	pad_id = tokenizer.eos_token_id or 0

	def forward_loss(model, input_ids, targets, mask, teacher_logits):
		logits = model(input_ids)[:, :-1, :]
		return kl_and_ce_loss(logits, teacher_logits, targets, mask, args.temperature, args.alpha)

	loss_and_grad_fn = nn.value_and_grad(student, forward_loss)

	def save_checkpoint(iteration: int):
		weights = dict(tree_flatten(student.trainable_parameters()))
		mx.save_safetensors(str(adapter_dir / f"{iteration}_adapters.safetensors"), weights)
		mx.save_safetensors(str(adapter_dir / "adapters.safetensors"), weights)
		print(f"Saved checkpoint at iteration {iteration}", file=sys.stderr)

	start_time = time.time()
	for it in range(completed_iterations + 1, args.iters + 1):
		batch = next(batches)
		input_ids, lengths, prompt_lens = pad_batch(batch, pad_id)
		batch_size, seq_len = input_ids.shape
		mask = build_loss_mask(batch_size, seq_len, lengths, prompt_lens)
		targets = input_ids[:, 1:]

		teacher_logits = mx.stop_gradient(teacher(input_ids)[:, :-1, :])

		(loss, kl, ce), grads = loss_and_grad_fn(student, input_ids, targets, mask, teacher_logits)
		optimizer.update(student, grads)
		mx.eval(student.parameters(), optimizer.state)

		if it == 1 or it % 10 == 0:
			elapsed = time.time() - start_time
			print(
				f"iter {it}/{args.iters}: loss={loss.item():.4f} kl={kl.item():.4f} "
				f"ce={ce.item():.4f} ({elapsed:.0f}s elapsed)",
				file=sys.stderr,
			)

		if it % args.save_every == 0 or it == args.iters:
			save_checkpoint(it)

	print("Training complete", file=sys.stderr)


if __name__ == "__main__":
	main()

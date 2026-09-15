# Production distillation result

Run date: 2026-08-27

Host: `valley-big-mac`, Apple M3 Ultra, 96 GB RAM

Remote artifact directory:
`/Users/aimac/.distill/results/manual-e2e-20260827`

## Experiment

The task classifies Moonshine scheduler logs into one of seven incident codes.
The training set contains 49 prompts. The frozen holdout contains 28 different
prompts, four per class. An exact normalized prompt comparison found no overlap.

Models and training settings:

- Teacher: `mlx-community/Qwen2.5-1.5B-Instruct-4bit`
- Student: `mlx-community/Qwen2.5-0.5B-Instruct-4bit`
- Method: live-logit KL distillation plus hard-label cross entropy
- Iterations: 150
- Batch size: 4
- Learning rate: `1e-5`
- Temperature: `2.0`
- KL weight: `0.5`
- LoRA rank and layers: 8 and 16
- Decoding: greedy, maximum 16 tokens
- Scoring: strict exact match

The teacher generated 37 of 49 training labels correctly. The pipeline compared
all generated labels with the deterministic answer key and replaced 12 wrong
labels before training. The teacher still supplied live logits during every
distillation step.

## Result

| Model | Correct | Accuracy |
|---|---:|---:|
| Base student | 7/28 | 25.0% |
| Distilled student | 18/28 | 64.3% |
| Teacher | 19/28 | 67.9% |

The distilled student improved by 39.3 percentage points over the base student.
The paired bootstrap 95% interval was +10.7 to +67.9 points. It won 16 cases,
regressed on 5, and tied on 7. A two-sided exact McNemar test gave `p = 0.0266`.

Per-class distilled scores were:

| Incident code | Correct |
|---|---:|
| MNS-101 network | 3/4 |
| MNS-202 memory | 2/4 |
| MNS-303 disk | 3/4 |
| MNS-404 input | 3/4 |
| MNS-505 authentication | 4/4 |
| MNS-606 tokenizer | 3/4 |
| MNS-707 timeout | 0/4 |

The result is a clear improvement on this small synthetic domain, but 28 cases
is still a pilot benchmark. The shared failure on all timeout cases suggests a
dataset or model weakness worth investigating before expanding the claim.

## Verification

The production MLX runtime passed all four distillation tests, including the EOS
regression added during this run. The evaluator independently recomputed every
saved prediction and matched `report.json`.

Artifact SHA-256 values:

- Holdout: `27c1ec1c53eb23f6350454fc8c970a2fe6f732db763322bf8f83113971aa2cc8`
- Training data: `98af94828bc140d70937edac429ba7817796fa6cf71fdb387baab5bd4bd90b7b`
- Final adapter: `09be0f5efcaa26c5be06f2d85d31ac33c20419ae0825135e146aa7d79371f398`
- JSON report: `166e56aca3cd7c9f02a664b97549f2e7188442372af8f3ea86b4ee40532bf23b`

An initial run exposed that the trainer did not append an EOS token. The model
often chose the correct code but continued generating, which produced 0% strict
exact-match accuracy. The trainer now appends EOS to each completion. The final
64.3% result comes from a clean retraining run after that fix.

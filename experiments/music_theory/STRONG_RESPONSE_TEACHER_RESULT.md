# Strong response-teacher result

Run date: 2026-08-28

Teacher generation host: local MacBook Pro, Apple M1 Max, 64 GB RAM

Student training and evaluation host: `valley-big-mac`, Apple M3 Ultra, 96 GB RAM

## Question

Does response distillation from the local Gemma 4 26B teacher improve the
Qwen2.5 0.5B student on the frozen music-theory holdout, and does it beat the
previous small-teacher logit run or the verified-label control?

## Setup

- Teacher: local `gemma-4-26b-qat-4bit`, 15 GB MLX checkpoint
- Student: `mlx-community/Qwen2.5-0.5B-Instruct-4bit`
- Training prompts: 217
- Frozen holdout prompts: 74
- LoRA iterations: 600
- Batch size: 4
- Learning rate: `1e-5`
- LoRA layers: 16
- Prompt loss: masked
- Decoding: greedy, maximum 48 teacher tokens and 40 student tokens
- Primary score: strict exact match

Gemma's reasoning channel was explicitly disabled. A one-question generation
smoke test returned `M3` for C to E before the full run started.

Two response-distillation variants were trained:

1. Raw response distillation used all 217 Gemma answers, including incorrect
   answers.
2. Verified response distillation kept only the 150 Gemma answers that matched
   the deterministic training answer key. The filter did not inspect or use
   any holdout answers.

The raw variant is the direct response-distillation experiment. The verified
variant is a diagnostic for label noise.

## Teacher quality

Gemma answered 150/217 training prompts correctly, or 69.1%. It answered 51/74
holdout prompts correctly, or 68.9%. The earlier Qwen2.5 1.5B teacher scored
5/74, or 6.8%, on the same holdout.

Gemma's holdout results by family were:

| Family | Correct |
|---|---:|
| Chord quality | 7/12 |
| Intervals | 17/26 |
| Key signatures | 8/8 |
| Triad spelling | 19/28 |

## Student result

| Model | Correct | Exact accuracy |
|---|---:|---:|
| Base student | 1/74 | 1.4% |
| Raw Gemma response-distilled | 10/74 | 13.5% |
| Verified Gemma response-distilled | 11/74 | 14.9% |
| Previous 1.5B logit-distilled | 11/74 | 14.9% |
| Previous full verified-label control | 12/74 | 16.2% |
| Gemma 26B teacher | 51/74 | 68.9% |

The raw response student improved by 12.2 percentage points over base. Its
paired bootstrap 95% interval was +4.1 to +20.3 points. It had 10 wins, one
regression, and 63 ties against base.

The verified response student improved by 13.5 points over base. Its paired
bootstrap 95% interval was +5.4 to +23.0 points. It had 11 wins, one regression,
and 62 ties against base.

Neither response variant beat the prior controls. Pairwise exact McNemar tests
found no measurable difference between raw response distillation, verified
response distillation, the old logit run, and the full verified-label control.
Every comparison had `p >= 0.77`.

## Results by family

| Model | Chord quality | Intervals | Key signatures | Triad spelling |
|---|---:|---:|---:|---:|
| Base | 0/12 | 1/26 | 0/8 | 0/28 |
| Raw response | 3/12 | 4/26 | 0/8 | 3/28 |
| Verified response | 3/12 | 5/26 | 0/8 | 3/28 |
| Gemma teacher | 7/12 | 17/26 | 8/8 | 19/28 |

The deterministic semantic scorer produced 5.4% for base, 13.5% for raw
response, 14.9% for verified response, and 68.9% for Gemma. Formatting does not
explain the gap.

## Conclusion

The stronger teacher fixed the teacher-quality problem, but response
distillation did not transfer most of that knowledge. The 0.5B student learned
the output vocabulary and some task patterns, then plateaued near the previous
controls. Filtering wrong teacher answers recovered one case but did not change
the conclusion.

For this benchmark, short answer response distillation is supervised
fine-tuning with noisy labels. The teacher's 26B parameters do not provide an
extra signal once its response has been reduced to a token such as `M3` or a
three-note string. The best tested result remains the full verified-label
control at 12/74.

A useful next experiment needs to change the transfer mechanism, not merely
increase teacher size. The clean choices are a larger student, verified
teacher-written explanations followed by answer-only training, or same-family
logit distillation from a capable Qwen teacher.

## Artifacts and verification

Local run directory:
`experiments/music_theory/runs/gemma4_26b_response_20260828`

Remote raw run:
`/Users/aimac/.distill/results/manual-music-theory-gemma26b-response-20260828`

Remote verified run:
`/Users/aimac/.distill/results/manual-music-theory-gemma26b-verified-response-20260828`

The evaluator regenerated all base and adapted-student answers, rejected
training and holdout prompt overlap, and wrote independent reports. The local
test suite passed with 12 tests and 8 MLX-only skips.

Artifact SHA-256 values:

- Raw teacher training responses: `233b12ac46e57f1ada88e289aaad0c6f11fd16310b081dce0bd3e9be2937575a`
- Verified teacher training responses: `78e5797434f695bb0265da505cfde8c7662e22605f3eb751acee8200fa7adb2b`
- Gemma holdout predictions: `b735ef9ea76d22321a911f3dcdf830286c6b104b824c5919153f0c521daabd6c`
- Raw evaluation report: `4b0dd776adab77dd1ddc14aa3ff331ac31846c1afc154f1dc57673e9c3c84d5d`
- Verified evaluation report: `0cbb88ec812040a7d5137c94d8728e172661c2aa9cae169101ad9a28f8c76451`
- Raw response adapter: `b5bfe3b04a41f255ca4e13549b136c370fff379760aee1c2082840938fee2f1f`
- Verified response adapter: `52ba7c836b7c6a38f5d4e3e4aea9ab327243d7d6781f1447e138975b62d54c19`

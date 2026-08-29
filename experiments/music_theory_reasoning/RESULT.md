# Music-theory distillation result

Run date: 2026-08-28 (America/Los_Angeles)

## Verdict

The 1,000-step training run improved the 2B student substantially over its base
model, but the teacher-logit term made it much worse than an otherwise matched
supervised fine-tune. For this teacher and dataset, the recommended recipe is
verified-response SFT, not 50/50 logit distillation.

## Experimental design

- Teacher: `mlx-community/Qwen3.5-35B-A3B-4bit` (35B-total MoE)
- Student: `mlx-community/Qwen3.5-2B-4bit`
- Real distillation: 50% temperature-2 KL plus 50% response CE
- Control: 0% KL plus 100% response CE
- Both arms: 1,000 iterations, batch size 1, learning rate `1e-5`, LoRA rank
  16, 24 adapted layers, and identical example order
- Training set: 1,000 prompts from 200 harmonic cases, with five paraphrases
  per case
- Held-out set: 80 prompts from 40 different harmonic cases, with two
  paraphrases per case
- Coverage: 20 harmonic-reasoning families, stratified across train and test
- Leakage checks: zero exact prompt overlap and zero `case_id` overlap
- Decoding: greedy, thinking disabled, maximum 384 generated tokens

The verified response, not the teacher's generated prose, supplied the hard
target in both training arms. The only experimental difference was whether the
student also matched the teacher's live token distribution.

Dataset SHA-256:

- Train: `964cc43d15ac9ae961f44700b7825caa30671e0a26fa74943249a00e0eb0df3f`
- Eval: `4a22a74e586de8b03a20c0bcd802179a97dab21a22d7ffadc755845fff51b440`

## Service runs

| Arm | Moonshine job | Status | Wall time | Final training diagnostics |
|---|---:|---:|---:|---|
| 50/50 logit distillation | `_fhkukIp0d` | complete | 15m 43s | loss 0.6819, KL 1.2322, CE 0.1316 |
| Supervised-only control | `SVBjNUnFQU` | complete | 15m 48s | loss/CE 0.0016, KL diagnostic 10.4987 |

Adapter SHA-256:

- Distilled: `015e60d73a95530c3aa777400f779ff452015fdc9eea7c1625c42bb0f7daef39`
- Control: `88a71825d4f2ca3ab0b029cba6928288c6c03404b0215d2d26dadf75bfb1977c`

## Held-out result

The primary metric is deterministic required-fact coverage. Pitch-name regexes
use strict, case-sensitive token boundaries. The reference answers score
400/400, confirming that the rubric recognizes its own gold data.

| Model | Required facts | Coverage | Fully correct cases | Average words |
|---|---:|---:|---:|---:|
| Base 2B | 70/400 | 17.5% | 0/80 | 231.5 |
| Supervised-only control | 376/400 | **94.0%** | **66/80** | 67.9 |
| 50/50 distilled 2B | 215/400 | 53.8% | 18/80 | 195.8 |
| 35B teacher | 156/400 | 39.0% | 0/80 | 231.7 |

Paired comparisons cluster the two paraphrases of each underlying case, giving
40 independent case clusters:

| Comparison | Fact-coverage delta | Clustered bootstrap 95% CI | Paired randomization p |
|---|---:|---:|---:|
| Control minus base | +76.5 points | +70.2 to +82.5 | 0.00001 |
| Distilled minus base | +36.2 points | +28.7 to +43.8 | 0.00001 |
| Distilled minus control | **-40.2 points** | **-49.0 to -31.5** | **0.00001** |

The conclusion holds in the all-required-facts metric too: control beats
distillation by 60.0 percentage points, with a clustered 95% interval from
45.0 to 73.8 points.

## Qualitative audit

The outputs explain the metric difference rather than merely differing in
style:

- On `F#m - G/B - C#7 - F#m`, both trained students give the correct concise
  `i - N6 - V7 - i` account, including G natural descending to E-sharp. The
  base invents chord tones and calls the Neapolitan a secondary dominant.
- On `C - F - Fm - C`, the teacher correctly identifies A-flat moving to G.
  The control omits the flat sign, while the distilled answer compounds that
  error by sending A to E and calling E scale degree 7. This is a real factual
  loss, not a missed keyword.
- On `Eb - Cm - F7 - Bb`, all generated variants expose weaknesses. The
  distilled answer calls Bb the relative minor of Eb and calls F the leading
  tone to Bb. The teacher calls Bb `IV` in Eb and F7 `V7/IV`. The verified
  analysis is `I - vi` in Eb, with Cm reinterpreted as `ii` in Bb, followed by
  `ii - V7 - I` in the new key.

The control often reproduces the concise verified analytical template. The
distilled model is nearly three times as verbose and mixes memorized correct
phrases with the teacher's confident harmonic errors.

## Interpretation

The control drove response CE almost to zero while its diagnostic divergence
from the teacher rose. That is exactly what the evaluation rewards: the
verified references are substantially more reliable than this teacher's music
theory output. The 50/50 run was forced to compromise between a good hard
target and a poor soft target, so it learned less of the verified domain.

This does not show that logit distillation is generally ineffective. It shows
that the current Qwen 35B teacher is not strong enough in this domain to justify
an alpha of 0.5. A sensible next experiment is an alpha sweep such as 0, 0.05,
0.1, and 0.25, or a genuinely expert music-theory teacher. The alpha-zero run
is already a strong successful domain fine-tune.

## Limitations

- The task set is synthetic and tests transposed, within-family transfer rather
  than unconstrained music analysis.
- Fact coverage is objective and reproducible but cannot capture every valid
  alternative explanation. Representative outputs were therefore audited
  directly.
- A separate blinded API judge was attempted, but the configured account
  returned persistent HTTP 429 responses before producing any judgments; no
  partial judge scores are included.

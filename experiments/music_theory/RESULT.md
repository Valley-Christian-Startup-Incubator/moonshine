# Music-theory distillation result

Run date: 2026-08-28

Host: `valley-big-mac`, Apple M3 Ultra, 96 GB RAM

Remote artifact directory:
`/Users/aimac/.distill/results/manual-music-theory-e2e-20260828`

## Benchmark

The benchmark covers conventional Western music theory:

- Ascending interval identification
- Diatonic triad spelling in major keys
- Major, minor, diminished, and augmented triad identification
- Major and minor key signatures

The generator created 217 training questions and 74 held-out questions. The
holdout contains 28 triad-spelling cases, 26 intervals, 12 chord-quality cases,
and 8 key signatures. There is no exact prompt overlap between training and
holdout data. The answer key comes from deterministic note, interval, scale,
and key-signature rules rather than model-generated answers.

## Configuration

- Teacher: `mlx-community/Qwen2.5-1.5B-Instruct-4bit`
- Student: `mlx-community/Qwen2.5-0.5B-Instruct-4bit`
- Iterations: 600
- Batch size: 4
- Learning rate: `1e-5`
- Temperature: `2.0`
- LoRA rank: 8
- LoRA layers: 16
- Decoding: greedy, maximum 40 tokens
- Primary scoring: strict exact match

Two matched adapters were trained:

- Logit distillation used `alpha=0.5`, splitting loss evenly between teacher KL
  and verified hard-label cross entropy.
- The control used `alpha=0.0`, so only verified hard labels affected training.

The teacher generated only 16 of 217 training answers correctly under strict
scoring, or 7.4%. The deterministic answer key replaced the other 201 answers.
The logit-distillation run still used the teacher's live probability
distribution at every training token.

## Primary result

| Model | Correct | Exact accuracy |
|---|---:|---:|
| Base student | 1/74 | 1.4% |
| Teacher | 5/74 | 6.8% |
| Logit-distilled student | 11/74 | 14.9% |
| Hard-label-only student | 12/74 | 16.2% |

Against the base student, logit distillation improved accuracy by 13.5 points.
The paired bootstrap 95% interval was +6.8 to +21.6 points. It won 10 cases,
lost none, and tied on 64. A two-sided exact McNemar test gave `p = 0.0020`.

The hard-label-only model improved by 14.9 points over base. Its 95% interval
was +6.8 to +23.0 points, with 11 wins and no regressions. McNemar's test gave
`p = 0.0010`.

Logit distillation scored 1.4 points below the hard-label-only control. The 95%
interval for that difference was -10.8 to +6.8 points. The models traded five
and six wins, with 63 ties and `p = 1.0`. There is no evidence that this
teacher's logits helped.

## Results by family

| Model | Chord quality | Intervals | Key signatures | Triad spelling |
|---|---:|---:|---:|---:|
| Base student | 0/12 | 1/26 | 0/8 | 0/28 |
| Teacher | 3/12 | 1/26 | 0/8 | 1/28 |
| Logit-distilled | 3/12 | 4/26 | 1/8 | 3/28 |
| Hard-label-only | 1/12 | 5/26 | 0/8 | 6/28 |

An exploratory deterministic semantic scorer tolerated capitalization,
punctuation, spaces around note separators, spelled-out interval names, and
equivalent accidental notation. It produced nearly the same result: base 5.4%,
teacher 8.1%, logit-distilled 14.9%, and hard-label-only 16.2%. The primary
claim remains based on the frozen exact-match scorer because the semantic rules
were written after viewing model outputs.

## Conclusion

The small student learned some music theory from verified examples, but this
run does not show successful knowledge distillation. The 1.5B teacher was weak
on the domain, and adding its token probabilities did not beat hard-label-only
training. The result supports supervised domain adaptation and demonstrates
why a teacher-quality baseline and an SFT control are necessary.

This is a combinatorial, within-distribution benchmark. It does not cover
harmonic analysis, voice leading, modulation, figured bass, or musical form.
A stronger follow-up should use a substantially more capable teacher and keep
this holdout untouched.

## Verification

The evaluator independently recomputed all 74 saved predictions. The deployed
Moonshine commit was `79200b7`. All four production MLX distillation tests
passed after the run. Dagu and the web app remained healthy on ports 8081 and
3001.

Artifact SHA-256 values:

- Training data: `caa389da0020243fa09abce12308824f0217bd00db13927124544213a34d1dc0`
- Frozen holdout: `1abb811dc3ebb87432865f5ad329c62465950b1e586d74c59dc0ed5b8e388df5`
- Teacher output: `91b3fe745a691758692ba99a81663e6d964fc1f40f687136e4c886014f3d6c34`
- Logit-distilled adapter: `f317fce9b9b1f608c5be123b411b7a3cc640a4e4084ab13e5aa8bcd3ccc7f856`
- Hard-label-only adapter: `7d41a04f4d746489abf185c12ff3d59cc4c11d7529357a2502727991d58db4ea`
- Exact distillation report: `4d67d899b6cf0a3834dcdb0e3e672467935be9c47cf0d3f3ada231636997286f`
- Exact hard-label report: `3f5a7090dcd2fbf52918314e5d7a9a10b9d39ddb51f7565f7e9cdd565cb4bc82`
- Exploratory semantic report: `d5d42747c94df5e95bf24122b071bb5043f6fd8fa2ca8b373c23b62d005b064e`

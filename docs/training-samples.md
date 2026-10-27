# Sampled training excerpts

The classroom job page can show the start of one example from a batch the Student has trained on. Fresh excerpts supply the globe's moving words and a readable card. Capture time and step identify the saved sample. The motion remains illustrative.

The feature covers classroom LoRA training through `train_eval.py`. It does not sample question generation, grading, or the separate Dagu logit-distillation trainer.

## Capture and configuration

`MOONSHINE_SAMPLE_INTERVAL_SECONDS` defaults to 5 seconds. Set it to `0` to disable capture. Positive values are clamped to 5 through 3600 seconds. Invalid or nonfinite values disable capture. Configure this in the worker's environment for future training processes; do not modify an active job to enable it.

The launcher retains the tokenizer already loaded by MLX. A dataset proxy observes a bounded prefix of CPU token IDs while the upstream iterator assembles a training batch. It publishes only after that batch's training update completes, including a successful final update. Validation and dataset sorting do not produce samples. There is no device readback, extra inference, or paid API request.

The writer limits excerpts to 64 tokens and 512 characters and atomically replaces `results/<job>/training_sample.json`. A decoding or delivery error disables capture for that attempt without retrying training. The parent remains the only writer of progress data. Each training attempt receives a new UUID so a resumed job cannot present an earlier attempt's sample as fresh.

The integration is enabled for mlx-lm 0.31.3 and 0.32.2, whose iterator contracts were checked. Other versions and unavailable optional hooks fall back to ordinary LoRA training. The fallback preserves training but produces no sample. Extending this version list requires checking the batching contract and running the sampler tests.

## Display and access

The server reads at most 4096 bytes and validates the snapshot, UUID, timestamp, and bounds. It uses the page's existing refresh; there is no new polling endpoint. Samples older than three capture intervals, with a minimum of 15 seconds, stop supplying moving text. A browser-only clock also stops fresh flow when page updates are delayed. Queued, paused, stopped, completed, and unavailable states are labeled explicitly.

Only the selected job owner's page data includes the excerpt. This follows the application's existing name convention, not independent user authentication. Everyone with the shared classroom password can select a name. Do not treat this as private access or use it to protect sensitive training text. Samples are not added to downloadable artifacts.

## Local measurement

The original [feasibility discussion](https://github.com/Valley-Christian-Startup-Incubator/moonshine/issues/3#issuecomment-6430419572) measured a simpler prototype. The user subsequently accepted small overhead and requested this implementation.

The implemented proxy and writer were measured on an Apple M1 Max using cached Qwen3-1.7B-4bit, MLX 0.32.2 and mlx-lm 0.31.3. Each run performed 100 real LoRA updates with batch size 1, 128-token synthetic inputs, four adapted layers, rank 8, dropout 0, and learning rate 1e-5. One warmup pair was excluded, followed by five alternating pairs.

| Mode | Median | Range |
| --- | --- | --- |
| Baseline | 13.888 seconds | 13.774 to 14.030 seconds |
| Sampling every five seconds | 13.809 seconds | 13.770 to 14.020 seconds |

The timing difference is within run-to-run variation. This does not establish a speedup or a precise slowdown bound. All saved adapter weights matched exactly. Every sampled run published an excerpt after the first interval. A separate capture-only test had a median of batch medians of 0.196 milliseconds, with batch medians from 0.185 to 0.206 milliseconds across five batches of 200 captures.

The machine was busy, including local development tools. This measures a small local LoRA workload, not a full classroom job or browser contention on the Mac Studio. No production benchmarks or deployment were performed. [Raw measured runs](training-samples-benchmark.csv) preserve the timing spread.

To repeat with a locally cached MLX model, run `python scripts/benchmark_training_samples.py --model /path/to/local/model --output-dir /tmp/moonshine-sample-benchmark`. This writes adapters, snapshots, a capture profile, and raw JSON results into the chosen output directory. Model loading, adapter reset, and output verification are outside the training timing. Training completion and final checkpoint writing are inside it on both sides.

## Verification

- `python3 -m unittest discover -s tests -p 'test_train*sample*.py' -v` exercises capture bounds, rate limits, consumed-batch ordering, validation exclusion, disabled and failed capture, final flush, and launcher fallback.
- `node --test web/tests/training-sample.test.mjs` checks read bounds, malformed data, other viewers, attempt identity, timestamps, and display states.
- `cd web && npm run check && npm run build` checks and builds the Svelte application.
- A real two-step run through `train_with_samples.py` exercised model loading, validation, training, and snapshot delivery locally.
- Authenticated local browser checks covered fresh flow, stale fallback, pause, resume identity, another selected name, and delayed server updates. The HTTP response for the other selected name contained no excerpt text.

#!/usr/bin/env python3
"""Run the upstream LoRA CLI with optional CPU-side training excerpts."""
from __future__ import annotations

import argparse
from importlib.metadata import version
import os
import sys

from training_samples import SampleWriter, sample_interval, sampling_iterator


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--sample-output", required=True)
    parser.add_argument("--sample-run-id", required=True)
    parser.add_argument("--sample-offset", type=int, default=0)
    options, remaining = parser.parse_known_args()
    sys.argv = [sys.argv[0], *remaining]

    from mlx_lm import lora
    writer = SampleWriter(options.sample_output, options.sample_run_id,
                          sample_interval(os.environ.get("MOONSHINE_SAMPLE_INTERVAL_SECONDS")),
                          options.sample_offset)
    try:
        supported = version("mlx-lm") in {"0.31.3", "0.32.2"}
        if supported and writer.enabled:
            from mlx_lm.tuner.datasets import CacheDataset
            from mlx_lm.tuner.trainer import iterate_batches
            supported = callable(getattr(lora, "load_dataset", None)) and callable(getattr(lora, "train", None))
    except (ImportError, AttributeError):
        supported = False
    if not writer.enabled or not supported:
        lora.main()
        return

    original_load, original_train = lora.load_dataset, lora.train

    def load_dataset(args, tokenizer):
        writer.decode = tokenizer.decode
        return original_load(args, tokenizer)

    def train(*args, **kwargs):
        if args or "iterate_batches" in kwargs:
            return original_train(*args, **kwargs)
        result = original_train(**kwargs, iterate_batches=sampling_iterator(iterate_batches, CacheDataset, writer))
        writer.publish_pending()
        return result

    lora.load_dataset, lora.train = load_dataset, train
    try:
        lora.main()
    finally:
        lora.load_dataset, lora.train = original_load, original_train


if __name__ == "__main__":
    main()

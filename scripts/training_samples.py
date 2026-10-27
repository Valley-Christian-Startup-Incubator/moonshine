"""Bounded, optional excerpts from CPU token IDs of consumed training batches."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import time


def sample_interval(value: str | None) -> float:
    try:
        seconds = float(value) if value is not None else 5.0
    except ValueError:
        return 0.0
    if not math.isfinite(seconds) or seconds <= 0:
        return 0.0
    return min(3600.0, max(5.0, seconds))


class SampleWriter:
    def __init__(self, path: str, run_id: str, interval: float, offset: int = 0):
        self.path = Path(path)
        self.run_id = run_id
        self.interval = interval
        self.offset = offset
        self.enabled = interval > 0
        self.decode = None
        self.next_capture = 0.0
        self.pending = None

    def due(self) -> bool:
        return self.enabled and self.decode is not None and time.monotonic() >= self.next_capture

    def publish_pending(self):
        pending, self.pending = self.pending, None
        if not self.enabled or pending is None:
            return
        tokens, step = pending
        temporary = self.path.with_name(f"{self.path.name}.tmp.{os.getpid()}")
        try:
            self.next_capture = time.monotonic() + self.interval
            text = re.sub(r"\s+", " ", self.decode(tokens, skip_special_tokens=True)).strip()[:512]
            if not text:
                return
            payload = {
                "version": 1, "runId": self.run_id,
                "capturedAt": datetime.now(timezone.utc).isoformat(),
                "step": self.offset + step, "intervalSeconds": self.interval,
                "tokenCount": len(tokens), "text": text,
            }
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w") as output:
                json.dump(payload, output, ensure_ascii=False)
            os.replace(temporary, self.path)
        except Exception:
            self.enabled = False
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def sampling_iterator(original, cache_type, writer: SampleWriter):
    class ObservedDataset(cache_type):
        def __init__(self, dataset):
            self.dataset = dataset
            self.armed = False
            self.tokens = None
            self.limit = 64

        def __len__(self):
            return len(self.dataset)

        def itemlen(self, index):
            return self.dataset.itemlen(index)

        def __getitem__(self, index):
            item = self.dataset[index]
            if self.armed:
                self.armed = False
                try:
                    tokens = item[0] if isinstance(item, tuple) else item
                    self.tokens = list(tokens[:min(self.limit, max(0, len(tokens) - 1))])
                except Exception:
                    writer.enabled = False
            return item

    def batches(dataset, batch_size, max_seq_length, loop=False, seed=None, comm_group=None):
        if not loop or not writer.enabled or not isinstance(dataset, cache_type):
            yield from original(dataset, batch_size, max_seq_length, loop, seed, comm_group)
            return
        observed = ObservedDataset(dataset)
        observed.limit = max(0, min(64, max_seq_length - 1))
        source = original(observed, batch_size, max_seq_length, loop, seed, comm_group)
        step = 0
        while True:
            writer.publish_pending()
            observed.armed = writer.due()
            observed.tokens = None
            try:
                batch = next(source)
            except StopIteration:
                return
            step += 1
            if observed.tokens:
                writer.pending = (observed.tokens, step)
            yield batch

    return batches

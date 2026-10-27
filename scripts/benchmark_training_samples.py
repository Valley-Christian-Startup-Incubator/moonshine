"""Compare local LoRA training with and without bounded CPU excerpts."""
import argparse
import cProfile
import json
from pathlib import Path
import platform
import statistics
import time

import mlx.core as mx
import mlx.optimizers as optim
import numpy as np
from mlx.utils import tree_flatten
from mlx_lm import load
from mlx_lm.tuner import trainer
from mlx_lm.tuner.utils import linear_to_lora_layers

from training_samples import SampleWriter, sampling_iterator
from mlx_lm.tuner.datasets import CacheDataset

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--model', required=True, help='Local MLX model directory')
parser.add_argument('--output-dir', required=True)
parser.add_argument('--iterations', type=int, default=100)
parser.add_argument('--pairs', type=int, default=5)
options = parser.parse_args()
if options.iterations < 1 or options.pairs < 1:
    parser.error('iterations and pairs must be positive')
ROOT = Path(options.output_dir)
ROOT.mkdir(parents=True, exist_ok=True)
MODEL = options.model
model, tokenizer = load(MODEL)
text = 'A student reads a question about the water cycle. Water evaporates from the ocean, condenses into clouds, and returns as rain. Explain how sunlight supplies energy for evaporation and why cooling causes condensation. '
ids = tokenizer.encode(text * 4)[:128]
assert len(ids) == 128
snapshot_path = str(ROOT / 'sample.json')

writer = SampleWriter(snapshot_path, '11111111-1111-4111-8111-111111111111', 5)
writer.decode = tokenizer.decode

def capture(tokens):
    writer.pending = (tokens[:64], 1)
    writer.publish_pending()
    assert writer.enabled

for _ in range(20):
    capture(ids)
micro = []
for run in range(5):
    durations = []
    for _ in range(200):
        start = time.perf_counter_ns()
        capture(ids)
        durations.append((time.perf_counter_ns() - start) / 1e6)
        assert json.loads(Path(snapshot_path).read_text())['text'] == tokenizer.decode(ids[:64], skip_special_tokens=True).strip()
    micro.append({'median_ms': statistics.median(durations),
                  'p95_ms': sorted(durations)[189], 'max_ms': max(durations)})
profile = cProfile.Profile()
profile.enable()
for _ in range(200):
    capture(ids)
profile.disable()
profile.dump_stats(str(ROOT / 'capture.prof'))
print('MICRO ' + json.dumps(micro), flush=True)

model.freeze()
linear_to_lora_layers(model, 4, {'rank': 8, 'dropout': 0.0, 'scale': 20.0})
mx.eval(model.parameters())
initial = dict(tree_flatten(model.trainable_parameters()))
mx.save_safetensors(str(ROOT / 'initial.safetensors'), initial)
class Data:
    def __len__(self):
        return 8
    def __getitem__(self, index):
        return text
    def process(self, row):
        return ids, 0

dataset = CacheDataset(Data())
results = []
reference = None
for run in range(2 + options.pairs * 2):
    mode = 'baseline' if run % 2 == 0 else 'sample_5s'
    model.load_weights(str(ROOT / 'initial.safetensors'), strict=False)
    mx.eval(model.parameters())
    mx.random.seed(42)
    np.random.seed(42)
    writer = SampleWriter(snapshot_path, '11111111-1111-4111-8111-111111111111', 5)
    writer.decode = tokenizer.decode
    sampled_iterator = sampling_iterator(trainer.iterate_batches, CacheDataset, writer)
    if mode != 'baseline':
        Path(snapshot_path).unlink(missing_ok=True)
    args = trainer.TrainingArgs(batch_size=1, iters=options.iterations, max_seq_length=128,
                               steps_per_report=10, steps_per_save=1000,
                               adapter_file=str(ROOT / 'trained.safetensors'))
    start = time.perf_counter()
    trainer.train(model, optim.Adam(learning_rate=1e-5), dataset, args=args,
                  iterate_batches=trainer.iterate_batches if mode == 'baseline' else sampled_iterator)
    if mode != 'baseline':
        writer.publish_pending()
    mx.eval(model.parameters())
    elapsed = time.perf_counter() - start
    weights = mx.load(str(ROOT / 'trained.safetensors'))
    if reference is None:
        reference = weights
    max_delta = max(float(mx.max(mx.abs(weights[k] - reference[k])).item()) for k in reference)
    assert max_delta < 1e-5, max_delta
    if mode != 'baseline':
        assert writer.enabled
        assert json.loads(Path(snapshot_path).read_text())['text'] == tokenizer.decode(ids[:64], skip_special_tokens=True).strip()
    result = {'run': run, 'warmup': run < 2, 'mode': mode, 'seconds': elapsed,
              'last_sample_step': json.loads(Path(snapshot_path).read_text())['step'] if mode != 'baseline' else None,
              'max_weight_delta': max_delta}
    results.append(result)
    print('RESULT ' + json.dumps(result), flush=True)
    (ROOT / 'results.json').write_text(json.dumps({'micro': micro, 'training': results,
        'model': MODEL, 'platform': platform.platform(), 'errors': 0,
        'snapshot_bytes': Path(snapshot_path).stat().st_size}, indent=2))

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { trainingProgress } from '../src/lib/training-progress.ts';

test('reads distillation and MLX reports without inventing progress', () => {
  assert.equal(trainingProgress('Loading student...', 1000).percent, null);
  const distill = trainingProgress('iter 1/100: loss=2.4500 kl=0.2\niter 10/100: loss=1.2000 kl=0.1', 1000);
  assert.equal(distill.percent, 10);
  assert.equal(distill.latest.loss, 1.2);
  const mlx = trainingProgress('Iter 10: Train loss 2.345, Learning Rate 1e-5, It/sec 1.5\nIter 20: Val loss 2.1', 200);
  assert.equal(mlx.latest.step, 10);
  assert.equal(mlx.percent, 5);
  assert.equal(trainingProgress('iter 1: loss=NaN', 100).latest, undefined);
});
test('clears old curves on restart and deduplicates reports', () => {
  const result = trainingProgress('iter 10: loss=2\niter 20: loss=1\niter 1: loss=3\niter 1: loss=2.9', 10);
  assert.deepEqual(result.samples, [{step: 1, loss: 2.9}]);
});

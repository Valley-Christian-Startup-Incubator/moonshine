import { strict as assert } from 'node:assert';
import { randomUUID } from 'node:crypto';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { trainingExcerptState } from '../src/lib/training-sample.ts';

test('sample reads reject other owners, old attempts, malformed and oversized snapshots', async () => {
	const root = await mkdtemp(path.join(tmpdir(), 'moonshine-sample-'));
	process.env.DISTILL_HOME = root;
	const { createServer } = await import('vite');
	const server = await createServer({ root: fileURLToPath(new URL('..', import.meta.url)), configFile: false, server: { middlewareMode: true }, appType: 'custom' });
	const folder = path.join(root, 'results', 'testjob1');
	const file = path.join(folder, 'training_sample.json');
	const now = Date.now();
	const sample = { version: 1, runId: randomUUID(), capturedAt: new Date(now - 1000).toISOString(), step: 21, intervalSeconds: 5, tokenCount: 10, text: 'Actual training text' };
	const options = { jobId: 'testjob1', runId: sample.runId, canView: true, attemptStartedAt: new Date(now - 2000).toISOString() };
	try {
		const { readTrainingSample } = await server.ssrLoadModule('/src/lib/server/training-sample.ts');
		await mkdir(folder, { recursive: true });
		await writeFile(file, JSON.stringify(sample));
		assert.deepEqual(await readTrainingSample(options, now), sample);
		assert.equal(await readTrainingSample({ ...options, canView: false }, now), null);
		assert.equal(await readTrainingSample({ ...options, runId: randomUUID() }, now), null);
		assert.equal(await readTrainingSample({ ...options, attemptStartedAt: new Date(now).toISOString() }, now), null);
		assert.equal(await readTrainingSample({ ...options, jobId: '../testjob1' }, now), null);
		for (const changes of [{ capturedAt: new Date(now + 1000).toISOString() }, { text: 'a'.repeat(513) }, { tokenCount: 65 }, { intervalSeconds: 0 }, { version: 2 }]) {
			await writeFile(file, JSON.stringify({ ...sample, ...changes }));
			assert.equal(await readTrainingSample(options, now), null);
		}
		await writeFile(file, 'x'.repeat(5000));
		assert.equal(await readTrainingSample(options, now), null);
		await writeFile(file, '{');
		assert.equal(await readTrainingSample(options, now), null);
		await rm(file);
		assert.equal(await readTrainingSample(options, now), null);
	} finally {
		await server.close();
		await rm(root, { recursive: true, force: true });
	}
});

test('text only flows for recent training samples with timely page updates', () => {
	const now = Date.now();
	const sample = { version: 1, runId: randomUUID(), capturedAt: new Date(now - 1000).toISOString(), step: 21, intervalSeconds: 5, tokenCount: 10, text: 'Actual training text' };
	const state = { sample, status: 'running', phase: 'train', isOwner: true, now, updatesDelayed: false, pendingAction: false };
	assert.equal(trainingExcerptState(state), 'recent');
	assert.equal(trainingExcerptState({ ...state, now: now + 15000 }), 'stale');
	assert.equal(trainingExcerptState({ ...state, updatesDelayed: true }), 'stale');
	assert.equal(trainingExcerptState({ ...state, status: 'paused' }), 'paused');
	assert.equal(trainingExcerptState({ ...state, status: 'queued' }), 'queued');
	assert.equal(trainingExcerptState({ ...state, pendingAction: true }), 'paused');
	assert.equal(trainingExcerptState({ ...state, phase: 'grade_base' }), 'finished');
	assert.equal(trainingExcerptState({ ...state, status: 'completed' }), 'finished');
	assert.equal(trainingExcerptState({ ...state, sample: null }), 'unavailable');
	assert.equal(trainingExcerptState({ ...state, isOwner: false }), 'hidden');
});

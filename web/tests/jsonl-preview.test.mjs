import { strict as assert } from 'node:assert';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { readJsonlPreview } from '../src/lib/server/jsonl-preview.ts';

test('preview pages preserve Unicode, blank lines, malformed entries, and final lines', async () => {
	const root = await mkdtemp(path.join(tmpdir(), 'moonshine-preview-'));
	const file = path.join(root, 'examples.jsonl');
	try {
		const values = Array.from({ length: 23 }, (_, i) => ({ prompt: `Question ${i} café 🌙`, completion: 'Answer\nwith details' }));
		await writeFile(file, '\uFEFF' + values.map((v) => JSON.stringify(v)).join('\r\n\n') + '\ninvalid JSON\nnull');
		const first = await readJsonlPreview(file);
		assert.equal(first.rows.length, 20);
		assert.deepEqual(first.rows[19].value, values[19]);
		const second = await readJsonlPreview(file, first.nextOffset);
		assert.deepEqual(second.rows[0].value, values[20]);
		assert.equal(second.rows[3].invalid, true);
		assert.equal(second.rows[4].value, null);
		assert.equal(second.rows[4].invalid, false);
		assert.equal(second.nextOffset, null);
		await assert.rejects(readJsonlPreview(file, 2), RangeError);
		await assert.rejects(readJsonlPreview(file, -1), RangeError);
		await writeFile(file, '');
		assert.deepEqual(await readJsonlPreview(file), { rows: [], nextOffset: null });
		await writeFile(file, JSON.stringify({ text: 'x'.repeat(1024 * 1024) }));
		await assert.rejects(readJsonlPreview(file), /too large/);
		const large = { text: '🌙'.repeat(150_000) };
		await writeFile(file, JSON.stringify(large) + '\n' + JSON.stringify(large));
		const bounded = await readJsonlPreview(file);
		assert.equal(bounded.rows.length, 1);
		assert.deepEqual(bounded.rows[0].value, large);
		const remainder = await readJsonlPreview(file, bounded.nextOffset);
		assert.deepEqual(remainder.rows[0].value, large);
		assert.equal(remainder.nextOffset, null);
	} finally {
		await rm(root, { recursive: true, force: true });
	}
});

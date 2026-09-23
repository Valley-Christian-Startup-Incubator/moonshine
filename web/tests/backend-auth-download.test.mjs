import { strict as assert } from 'node:assert';
import { createHash } from 'node:crypto';
import { mkdir, mkdtemp, readFile, rm, stat, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { test } from 'node:test';

test('shared password sessions and result path checks', async () => {
	const root = await mkdtemp(path.join(tmpdir(), 'moonshine-backend-'));
	const distillHome = path.join(root, 'distill');
	process.env.DISTILL_HOME = distillHome;
	process.env.WEB_PASSWORD = 'shared-password';

	const auth = await import('../src/lib/server/auth.ts');
	const downloads = await import('../src/lib/server/downloads.ts');
	try {
		assert.deepEqual(auth.authenticateUser('shared-password'), { id: 'shared' });
		assert.equal(auth.authenticateUser('wrong-password'), null);
		assert.equal(auth.authenticateUser(''), null);
		assert.equal(auth.safeNextPath('/jobs/a?tab=log'), '/jobs/a?tab=log');
		assert.equal(auth.safeNextPath('//evil.example/'), '/');
		assert.equal(auth.safeNextPath('/\\evil.example/'), '/');

		const token = await auth.createSession();
		assert.match(token, /^[A-Za-z0-9_-]{43}$/);
		assert.deepEqual(await auth.resolveSession(token), { id: 'shared' });
		const sessionFile = path.join(
			auth.SESSIONS_DIR,
			`${createHash('sha256').update(token).digest('hex')}.json`
		);
		assert.equal((await stat(sessionFile)).mode & 0o777, 0o600);
		const sessionText = await readFile(sessionFile, 'utf8');
		assert.equal(sessionText.includes(token), false);
		assert.equal(sessionText.includes('shared-password'), false);

		await auth.revokeSession(token);
		assert.equal(await auth.resolveSession(token), null);
		const expiredToken = await auth.createSession();
		const expiredPath = path.join(
			auth.SESSIONS_DIR,
			`${createHash('sha256').update(expiredToken).digest('hex')}.json`
		);
		const expiredRecord = JSON.parse(await readFile(expiredPath, 'utf8'));
		expiredRecord.expiresAt = Date.now() - 1;
		await writeFile(expiredPath, JSON.stringify(expiredRecord));
		assert.equal(await auth.resolveSession(expiredToken), null);

		process.env.WEB_PASSWORD = '';
		const unconfiguredAuth = await import('../src/lib/server/auth.ts?missing-password');
		assert.equal(unconfiguredAuth.authenticateUser('shared-password'), null);

		const resultDir = path.join(distillHome, 'results', 'job-1');
		const nestedDir = path.join(resultDir, 'nested', 'adapters');
		await mkdir(nestedDir, { recursive: true });
		await writeFile(path.join(nestedDir, 'weights.bin'), 'weights');
		const validated = await downloads.validateResultPath('job-1', nestedDir);
		assert.equal(validated.stats.isDirectory(), true);

		const outside = path.join(root, 'outside.txt');
		await writeFile(outside, 'outside');
		await symlink(outside, path.join(nestedDir, 'escape.txt'));
		await assert.rejects(
			downloads.validateResultPath('job-1', nestedDir),
			(error) => error instanceof downloads.UnsafeResultPathError
		);
		await assert.rejects(
			downloads.validateResultPath('job-1', outside),
			(error) => error instanceof downloads.UnsafeResultPathError
		);
	} finally {
		await rm(root, { recursive: true, force: true });
	}
});

import { strict as assert } from 'node:assert';
import { createHash } from 'node:crypto';
import { mkdir, mkdtemp, readFile, readdir, rm, stat, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { test } from 'node:test';

test('account credentials, invite registration, sessions, and result path checks', async () => {
	const root = await mkdtemp(path.join(tmpdir(), 'moonshine-backend-'));
	const distillHome = path.join(root, 'distill');
	process.env.DISTILL_HOME = distillHome;
	process.env.WEB_PASSWORD = 'classroom-invite';

	const auth = await import('../src/lib/server/auth.ts');
	const downloads = await import('../src/lib/server/downloads.ts');
	try {
		assert.equal(auth.normalizeUsername('Alice_01'), 'alice_01');
		assert.equal(auth.normalizeUsername('ab'), null);
		assert.equal(auth.normalizeUsername('bad name'), null);
		assert.equal(auth.normalizeUsername('Kelvin'), null);
		assert.equal(auth.safeNextPath('/jobs/a?tab=log'), '/jobs/a?tab=log');
		assert.equal(auth.safeNextPath('//evil.example/'), '/');
		assert.equal(auth.safeNextPath('/\\evil.example/'), '/');

		await assert.rejects(
			auth.registerAccount('Alice_01', 'long-enough-password', 'wrong-invite'),
			(error) => error instanceof auth.InvalidInviteCodeError
		);
		const user = await auth.registerAccount('Alice_01', 'long-enough-password', 'classroom-invite');
		assert.equal(user.username, 'alice_01');
		assert.deepEqual(await auth.authenticateUser('ALICE_01', 'long-enough-password'), user);
		assert.equal(await auth.authenticateUser('alice_01', 'wrong-password'), null);

		const accountFiles = await readdir(auth.ACCOUNTS_DIR);
		assert.deepEqual(accountFiles, ['alice_01.json']);
		assert.equal((await stat(path.join(auth.ACCOUNTS_DIR, accountFiles[0]))).mode & 0o777, 0o600);
		const accountText = await readFile(path.join(auth.ACCOUNTS_DIR, accountFiles[0]), 'utf8');
		assert.equal(accountText.includes('long-enough-password'), false);
		assert.equal(accountText.includes('classroom-invite'), false);

		const attempts = await Promise.allSettled(
			Array.from({ length: 8 }, () =>
				auth.registerAccount('Race_User', 'long-enough-password', 'classroom-invite')
			)
		);
		assert.equal(attempts.filter((attempt) => attempt.status === 'fulfilled').length, 1);
		assert.equal(attempts.filter((attempt) => attempt.status === 'rejected').length, 7);

		const token = await auth.createSession(user);
		assert.match(token, /^[A-Za-z0-9_-]{43}$/);
		assert.deepEqual(await auth.resolveSession(token), user);
		const sessionFile = path.join(
			auth.SESSIONS_DIR,
			`${createHash('sha256').update(token).digest('hex')}.json`
		);
		assert.equal((await stat(sessionFile)).mode & 0o777, 0o600);
		assert.equal((await readFile(sessionFile, 'utf8')).includes(token), false);
		await auth.revokeSession(token);
		assert.equal(await auth.resolveSession(token), null);
		const expiredToken = await auth.createSession(user);
		const expiredPath = path.join(
			auth.SESSIONS_DIR,
			`${createHash('sha256').update(expiredToken).digest('hex')}.json`
		);
		const expiredRecord = JSON.parse(await readFile(expiredPath, 'utf8'));
		expiredRecord.expiresAt = Date.now() - 1;
		await writeFile(expiredPath, JSON.stringify(expiredRecord));
		assert.equal(await auth.resolveSession(expiredToken), null);

		process.env.WEB_PASSWORD = '';
		const openAuth = await import('../src/lib/server/auth.ts?open-registration');
		const openUser = await openAuth.registerAccount('Open_User', 'another-long-password', '');
		assert.equal(openUser.username, 'open_user');

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

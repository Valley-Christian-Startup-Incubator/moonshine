import { createHash, randomBytes, timingSafeEqual } from 'node:crypto';
import { chmod, mkdir, open, readFile, unlink, lstat } from 'node:fs/promises';
import path from 'node:path';

const DISTILL_HOME = process.env.DISTILL_HOME ?? `${process.env.HOME}/.distill`;
const WEB_PASSWORD = process.env.WEB_PASSWORD ?? '';

export const WEB_SESSION_COOKIE = 'distill_session';
export const SESSION_MAX_AGE_SECONDS = 60 * 60 * 12;
export const SESSION_MAX_AGE_MS = SESSION_MAX_AGE_SECONDS * 1000;
export const AUTH_DIR = path.join(DISTILL_HOME, 'auth');
export const SESSIONS_DIR = path.join(AUTH_DIR, 'sessions');

const SESSION_TOKEN_PATTERN = /^[A-Za-z0-9_-]{43}$/;
const SHARED_USER = { id: 'shared' } as const;

export interface AuthenticatedUser {
	id: string;
}

interface SessionRecord {
	expiresAt: number;
	createdAt: string;
}

function digest(value: string): Buffer {
	return createHash('sha256').update(value).digest();
}

function safeTimingEqual(left: Buffer, right: Buffer): boolean {
	return left.length === right.length && timingSafeEqual(left, right);
}

function sessionPath(token: string): string {
	return path.join(SESSIONS_DIR, `${digest(token).toString('hex')}.json`);
}

async function ensurePrivateDirectory(directory: string): Promise<void> {
	await mkdir(directory, { recursive: true, mode: 0o700 });
	await chmod(directory, 0o700);
}

async function ensureAuthDirectories(): Promise<void> {
	await ensurePrivateDirectory(AUTH_DIR);
	await ensurePrivateDirectory(SESSIONS_DIR);
}

function parseJson<T>(raw: string): T | null {
	try { return JSON.parse(raw) as T; }
	catch { return null; }
}

function isValidSessionRecord(record: SessionRecord | null): record is SessionRecord {
	return Boolean(record && typeof record.expiresAt === 'number' && Number.isFinite(record.expiresAt));
}

async function readPrivateJson<T>(filePath: string): Promise<T | null> {
	try {
		const fileStats = await lstat(filePath);
		if (!fileStats.isFile()) return null;
		await chmod(filePath, 0o600);
		return parseJson<T>(await readFile(filePath, 'utf8'));
	} catch {
		return null;
	}
}

async function writeExclusive(filePath: string, contents: string): Promise<void> {
	const handle = await open(filePath, 'wx', 0o600);
	try {
		await handle.writeFile(contents, 'utf8');
		await handle.chmod(0o600);
	} finally {
		await handle.close();
	}
}

/** Validate the one shared password without storing it in a browser session. */
export function authenticateUser(password: unknown): AuthenticatedUser | null {
	if (!WEB_PASSWORD || typeof password !== 'string') return null;
	return safeTimingEqual(digest(password), digest(WEB_PASSWORD)) ? SHARED_USER : null;
}

/** Create a random, expiring session and return only its raw bearer token. */
export async function createSession(): Promise<string> {
	await ensureAuthDirectories();
	for (;;) {
		const token = randomBytes(32).toString('base64url');
		const record: SessionRecord = {
			createdAt: new Date().toISOString(),
			expiresAt: Date.now() + SESSION_MAX_AGE_MS
		};
		try {
			await writeExclusive(sessionPath(token), JSON.stringify(record));
			return token;
		} catch (error) {
			if ((error as NodeJS.ErrnoException).code === 'EEXIST') continue;
			throw error;
		}
	}
}

/** Resolve an existing session. Legacy account sessions remain valid until expiry. */
export async function resolveSession(token: string | undefined): Promise<AuthenticatedUser | null> {
	if (!token || !SESSION_TOKEN_PATTERN.test(token)) return null;
	const filePath = sessionPath(token);
	const record = await readPrivateJson<SessionRecord>(filePath);
	if (!isValidSessionRecord(record)) return null;
	if (record.expiresAt <= Date.now()) {
		await unlink(filePath).catch(() => undefined);
		return null;
	}
	return SHARED_USER;
}

export const getUserFromSession = resolveSession;

export async function revokeSession(token: string | undefined): Promise<void> {
	if (!token || !SESSION_TOKEN_PATTERN.test(token)) return;
	await unlink(sessionPath(token)).catch(() => undefined);
}

export function safeNextPath(value: unknown): string {
	if (
		typeof value !== 'string' ||
		!value.startsWith('/') ||
		value.startsWith('//') ||
		value.includes('\\') ||
		value.includes('\r') ||
		value.includes('\n')
	) return '/';
	return value;
}

interface FailureState { count: number; lastFailureAt: number }
const failedLogins = new Map<string, FailureState>();
const FAILURE_WINDOW_MS = 60_000;
const FAILURE_DELAY_MS = 250;
const MAX_FAILURE_DELAY_MS = 4_000;
const MAX_FAILURE_KEYS = 10_000;

function failureKey(value: string): string {
	return digest(value).toString('hex');
}

export async function waitForLoginThrottle(key: string): Promise<void> {
	const now = Date.now();
	const state = failedLogins.get(failureKey(key));
	if (!state || now - state.lastFailureAt > FAILURE_WINDOW_MS || state.count < 5) return;
	const delay = Math.min(MAX_FAILURE_DELAY_MS, FAILURE_DELAY_MS * 2 ** (state.count - 5));
	await new Promise((resolve) => setTimeout(resolve, delay));
}

export function isLoginThrottled(key: string): boolean {
	const state = failedLogins.get(failureKey(key));
	return Boolean(state && Date.now() - state.lastFailureAt <= FAILURE_WINDOW_MS && state.count >= 12);
}

export function noteLoginFailure(key: string): void {
	const now = Date.now();
	for (const [knownKey, state] of failedLogins) {
		if (now - state.lastFailureAt > FAILURE_WINDOW_MS) failedLogins.delete(knownKey);
	}
	if (failedLogins.size >= MAX_FAILURE_KEYS && !failedLogins.has(failureKey(key))) {
		const oldestKey = failedLogins.keys().next().value;
		if (oldestKey) failedLogins.delete(oldestKey);
	}
	const hashedKey = failureKey(key);
	const previous = failedLogins.get(hashedKey);
	failedLogins.set(hashedKey, {
		count: previous && now - previous.lastFailureAt <= FAILURE_WINDOW_MS ? Math.min(previous.count + 1, 12) : 1,
		lastFailureAt: now
	});
}

export function clearLoginFailures(key: string): void {
	failedLogins.delete(failureKey(key));
}

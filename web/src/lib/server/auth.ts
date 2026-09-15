import {
	createHash,
	randomBytes,
	scrypt as scryptCallback,
	timingSafeEqual
} from 'node:crypto';
import { chmod, mkdir, open, readFile, unlink, lstat } from 'node:fs/promises';
import path from 'node:path';

const DISTILL_HOME = process.env.DISTILL_HOME ?? `${process.env.HOME}/.distill`;
const WEB_PASSWORD = process.env.WEB_PASSWORD ?? '';

const scrypt = (password: string, salt: Buffer, keylen: number): Promise<Buffer> =>
	new Promise((resolve, reject) => {
		scryptCallback(
			password,
			salt,
			keylen,
			{ N: 16_384, r: 8, p: 1, maxmem: 32 * 1024 * 1024 },
			(error, derivedKey) => (error ? reject(error) : resolve(derivedKey))
		);
	});

export const WEB_SESSION_COOKIE = 'distill_session';
export const SESSION_MAX_AGE_SECONDS = 60 * 60 * 12;
export const SESSION_MAX_AGE_MS = SESSION_MAX_AGE_SECONDS * 1000;
export const AUTH_DIR = path.join(DISTILL_HOME, 'auth');
export const ACCOUNTS_DIR = path.join(AUTH_DIR, 'accounts');
export const SESSIONS_DIR = path.join(AUTH_DIR, 'sessions');

export const MIN_USERNAME_LENGTH = 3;
export const MAX_USERNAME_LENGTH = 32;
export const MIN_PASSWORD_LENGTH = 10;
export const MAX_PASSWORD_LENGTH = 256;

const USERNAME_PATTERN = /^[a-z0-9_-]{3,32}$/;
const SESSION_TOKEN_PATTERN = /^[A-Za-z0-9_-]{43}$/;
const PASSWORD_HASH_BYTES = 64;
const PASSWORD_SALT_BYTES = 16;

/** The only user information exposed to request handlers and the browser. */
export interface AuthenticatedUser {
	id: string;
	username: string;
}

interface AccountRecord extends AuthenticatedUser {
	salt: string;
	passwordHash: string;
	createdAt: string;
}

interface SessionRecord extends AuthenticatedUser {
	expiresAt: number;
	createdAt: string;
}

export class AccountExistsError extends Error {
	constructor() {
		super('An account with that username already exists.');
		this.name = 'AccountExistsError';
	}
}

export class InvalidUsernameError extends Error {
	constructor() {
		super('Username must be 3–32 ASCII letters, numbers, underscores, or hyphens.');
		this.name = 'InvalidUsernameError';
	}
}

export class InvalidPasswordError extends Error {
	constructor() {
		super('Password must be between 10 and 256 characters.');
		this.name = 'InvalidPasswordError';
	}
}

export class InvalidInviteCodeError extends Error {
	constructor() {
		super('The invite code is invalid.');
		this.name = 'InvalidInviteCodeError';
	}
}

function digest(value: string): Buffer {
	return createHash('sha256').update(value).digest();
}

function safeTimingEqual(left: Buffer, right: Buffer): boolean {
	return left.length === right.length && timingSafeEqual(left, right);
}

function accountPath(username: string): string {
	return path.join(ACCOUNTS_DIR, `${username}.json`);
}

function sessionPath(token: string): string {
	return path.join(SESSIONS_DIR, `${digest(token).toString('hex')}.json`);
}

async function ensurePrivateDirectory(directory: string): Promise<void> {
	await mkdir(directory, { recursive: true, mode: 0o700 });
	// mkdir's mode is affected by the process umask and does not change an
	// existing directory. Explicitly restore private permissions on every use.
	await chmod(directory, 0o700);
}

async function ensureAuthDirectories(): Promise<void> {
	await ensurePrivateDirectory(AUTH_DIR);
	await ensurePrivateDirectory(ACCOUNTS_DIR);
	await ensurePrivateDirectory(SESSIONS_DIR);
}

function parseJson<T>(raw: string): T | null {
	try {
		return JSON.parse(raw) as T;
	} catch {
		return null;
	}
}

function isValidUserRecord(record: AccountRecord | null, username: string): record is AccountRecord {
	return Boolean(
		record &&
		typeof record.id === 'string' &&
		record.id.length > 0 &&
		record.username === username &&
		typeof record.salt === 'string' &&
		typeof record.passwordHash === 'string' &&
		Buffer.from(record.salt, 'base64').length === PASSWORD_SALT_BYTES &&
		Buffer.from(record.passwordHash, 'base64').length === PASSWORD_HASH_BYTES
	);
}

function isValidSessionRecord(record: SessionRecord | null): record is SessionRecord {
	return Boolean(
		record &&
		typeof record.id === 'string' &&
		typeof record.username === 'string' &&
		USERNAME_PATTERN.test(record.username) &&
		typeof record.expiresAt === 'number' &&
		Number.isFinite(record.expiresAt)
	);
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

/** Normalize a username while enforcing the deliberately small account-name alphabet. */
export function normalizeUsername(value: unknown): string | null {
	if (typeof value !== 'string') return null;
	if (!/^[A-Za-z0-9_-]{3,32}$/.test(value)) return null;
	const username = value.toLowerCase();
	return USERNAME_PATTERN.test(username) ? username : null;
}

export function isValidPassword(value: unknown): value is string {
	return (
		typeof value === 'string' &&
		value.length >= MIN_PASSWORD_LENGTH &&
		value.length <= MAX_PASSWORD_LENGTH
	);
}

export function isInviteRequired(): boolean {
	return WEB_PASSWORD.length > 0;
}

async function derivePasswordHash(password: string, salt: Buffer): Promise<Buffer> {
	return scrypt(password, salt, PASSWORD_HASH_BYTES);
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

/**
 * Register an account. The account file is created with O_EXCL so concurrent
 * registrations for the same normalized username cannot overwrite each other.
 */
export async function registerAccount(
	rawUsername: unknown,
	password: unknown,
	inviteCode: unknown
): Promise<AuthenticatedUser> {
	const username = normalizeUsername(rawUsername);
	if (!username) throw new InvalidUsernameError();
	if (!isValidPassword(password)) throw new InvalidPasswordError();
	if (isInviteRequired() && !safeTimingEqual(digest(String(inviteCode ?? '')), digest(WEB_PASSWORD))) {
		throw new InvalidInviteCodeError();
	}

	await ensureAuthDirectories();
	const user: AuthenticatedUser = {
		id: randomBytes(16).toString('hex'),
		username
	};
	const salt = randomBytes(PASSWORD_SALT_BYTES);
	const passwordHash = await derivePasswordHash(password, salt);
	const record: AccountRecord = {
		...user,
		salt: salt.toString('base64'),
		passwordHash: passwordHash.toString('base64'),
		createdAt: new Date().toISOString()
	};

	try {
		await writeExclusive(accountPath(username), JSON.stringify(record));
	} catch (error) {
		if ((error as NodeJS.ErrnoException).code === 'EEXIST') {
			throw new AccountExistsError();
		}
		throw error;
	}

	return user;
}

// A fixed dummy salt keeps the shape of nonexistent-user password checks close
// to real checks without retaining any credential material.
const DUMMY_SALT = Buffer.alloc(PASSWORD_SALT_BYTES, 0);

/** Return a user for valid credentials, or null for every invalid credential case. */
export async function authenticateUser(
	rawUsername: unknown,
	password: unknown
): Promise<AuthenticatedUser | null> {
	const username = normalizeUsername(rawUsername);
	if (!isValidPassword(password)) return null;
	await ensureAuthDirectories();

	const record = await readPrivateJson<AccountRecord>(accountPath(username ?? 'invalid'));
	if (!isValidUserRecord(record, username ?? '')) {
		await derivePasswordHash(password, DUMMY_SALT);
		return null;
	}

	const expected = Buffer.from(record.passwordHash, 'base64');
	const actual = await derivePasswordHash(password, Buffer.from(record.salt, 'base64'));
	return safeTimingEqual(actual, expected) ? { id: record.id, username: record.username } : null;
}

/** Create a random, expiring session and return only its raw bearer token. */
export async function createSession(user: AuthenticatedUser): Promise<string> {
	if (!normalizeUsername(user.username) || user.username !== normalizeUsername(user.username)) {
		throw new Error('Invalid session user.');
	}
	await ensureAuthDirectories();

	for (;;) {
		const token = randomBytes(32).toString('base64url');
		const record: SessionRecord = {
			id: user.id,
			username: user.username,
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

/** Resolve and validate a session token from the browser cookie. */
export async function resolveSession(token: string | undefined): Promise<AuthenticatedUser | null> {
	if (!token || !SESSION_TOKEN_PATTERN.test(token)) return null;
	const filePath = sessionPath(token);
	const record = await readPrivateJson<SessionRecord>(filePath);
	if (!isValidSessionRecord(record)) return null;
	if (record.expiresAt <= Date.now()) {
		await unlink(filePath).catch(() => undefined);
		return null;
	}
	return { id: record.id, username: record.username };
}

export const getUserFromSession = resolveSession;

/** Revoke a session without exposing whether the token existed. */
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
	) {
		return '/';
	}
	return value;
}

interface FailureState {
	count: number;
	lastFailureAt: number;
}

const failedLogins = new Map<string, FailureState>();
const FAILURE_WINDOW_MS = 60_000;
const FAILURE_DELAY_MS = 250;
const MAX_FAILURE_DELAY_MS = 4_000;
const MAX_FAILURE_KEYS = 10_000;

function failureKey(value: string): string {
	return digest(value).toString('hex');
}

/** Delay repeated failures modestly so credential guessing does not run freely. */
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
	if (!previous || now - previous.lastFailureAt > FAILURE_WINDOW_MS) {
		failedLogins.set(hashedKey, { count: 1, lastFailureAt: now });
		return;
	}
	failedLogins.set(hashedKey, {
		count: Math.min(previous.count + 1, 12),
		lastFailureAt: now
	});
}

export function clearLoginFailures(key: string): void {
	failedLogins.delete(failureKey(key));
}

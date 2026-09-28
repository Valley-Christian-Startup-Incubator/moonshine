import path from 'node:path';
import { stat } from 'node:fs/promises';
import { RESULTS_DIR, USERS_FILE, readJsonFile, writeJsonAtomic } from './moonshine-paths';
import { listJobs, purgeJob } from './queue';
import { deleteRegisteredModel, listRegisteredModels } from './ollama';
import { scrubLogsAfterPurge, type ScrubResult } from './log-scrub';

/**
 * Names replace teams. After the shared-password login the person picks their
 * name (or adds one). Names live in ~/.distill/users.json; the choice is kept
 * in the `moonshine_user` cookie and re-validated on every request.
 */
export const USER_COOKIE = 'moonshine_user';
export const USER_COOKIE_MAX_AGE = 60 * 60 * 24 * 180;

export interface UserRecord {
	name: string;
	createdAt: string;
}

type CookieJar = {
	get(name: string): string | undefined;
	set(name: string, value: string, opts: { path: string; httpOnly?: boolean; sameSite?: 'lax' | 'strict'; secure?: boolean; maxAge?: number }): void;
	delete(name: string, opts: { path: string }): void;
};

export function normalizeName(raw: unknown): string | null {
	if (typeof raw !== 'string') return null;
	const name = raw.normalize('NFC').replace(/\s+/g, ' ').trim();
	if (name.length < 1 || name.length > 40) return null;
	if (!/^[\p{L}\p{N} .'\-]+$/u.test(name)) return null;
	if (!/[\p{L}\p{N}]/u.test(name)) return null;
	return name;
}

export async function listUsers(): Promise<UserRecord[]> {
	const users = await readJsonFile<UserRecord[]>(USERS_FILE, []);
	return [...users].sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }));
}

export async function findUser(name: string): Promise<UserRecord | null> {
	const key = name.toLowerCase();
	return (await listUsers()).find((u) => u.name.toLowerCase() === key) ?? null;
}

/** Add a name (case-insensitive unique). Returns the stored record (existing one if already present). */
export async function addUser(raw: unknown): Promise<UserRecord> {
	const name = normalizeName(raw);
	if (!name) throw new Error("Use 1–40 letters, numbers, spaces, apostrophes, dots or dashes.");
	const existing = await findUser(name);
	if (existing) return existing;
	const users = await readJsonFile<UserRecord[]>(USERS_FILE, []);
	const rec = { name, createdAt: new Date().toISOString() };
	await writeJsonAtomic(USERS_FILE, [...users, rec]);
	return rec;
}

/** The currently selected name from the cookie, or null if unset / deleted. */
export async function getCurrentUserName(cookies: Pick<CookieJar, 'get'>): Promise<string | null> {
	const raw = cookies.get(USER_COOKIE);
	if (!raw) return null;
	let name: string;
	try {
		name = decodeURIComponent(raw);
	} catch {
		return null;
	}
	return (await findUser(name))?.name ?? null;
}

export async function selectUser(cookies: CookieJar, raw: unknown, secure: boolean): Promise<UserRecord> {
	const name = normalizeName(raw);
	const user = name ? await findUser(name) : null;
	if (!user) throw new Error('Pick a name from the list or add a new one.');
	cookies.set(USER_COOKIE, encodeURIComponent(user.name), {
		path: '/',
		httpOnly: true,
		sameSite: 'lax',
		secure,
		maxAge: USER_COOKIE_MAX_AGE
	});
	return user;
}

export function clearSelectedUser(cookies: CookieJar): void {
	cookies.delete(USER_COOKIE, { path: '/' });
}

export interface DeleteUserResult {
	name: string;
	jobsPurged: number;
	runningOrQueuedCancelled: number;
	ollamaModelsDeleted: string[];
	ollamaModelsFailed: string[];
	/** Log lines removed that mentioned the name, its slug, its job IDs or model tags. */
	logScrub: ScrubResult;
}

export interface DeleteUserPreview {
	name: string;
	jobs: number;
	activeJobs: number;
	resultFolders: number;
	ollamaModels: string[];
	jobList: { id: string; type: string; status: string; submittedAt: string }[];
}

/** What deleting a name would remove (for the admin warning popup). */
export async function previewDeleteUser(name: string): Promise<DeleteUserPreview> {
	const jobs = await listJobs({ ownerName: name });
	let resultFolders = 0;
	for (const j of jobs) {
		if (await stat(path.join(RESULTS_DIR, j.id)).then((s) => s.isDirectory()).catch(() => false)) resultFolders++;
	}
	return {
		name,
		jobs: jobs.length,
		activeJobs: jobs.filter((j) => ['queued', 'running', 'paused'].includes(j.status)).length,
		resultFolders,
		ollamaModels: (await listRegisteredModels(name)).map((m) => m.tag),
		jobList: jobs.map((j) => ({ id: j.id, type: j.type, status: j.status, submittedAt: j.submittedAt }))
	};
}

/**
 * Admin only. Deletes the name, cancels that person's running/queued/paused
 * jobs, deletes all their job + result files (the queue runner stops a running
 * job first, then deletes), and deletes the Ollama models Moonshine registered
 * for them. Base models are never touched. Finally scrubs the name, its slug,
 * its job IDs and model tags from ~/.distill/logs/*.log (in place).
 */
export async function deleteUser(name: string): Promise<DeleteUserResult> {
	const user = await findUser(name);
	if (!user) throw new Error('No such name.');
	const users = await readJsonFile<UserRecord[]>(USERS_FILE, []);
	await writeJsonAtomic(
		USERS_FILE,
		users.filter((u) => u.name !== user.name)
	);
	const jobs = await listJobs({ ownerName: user.name });
	for (const j of jobs) await purgeJob(j.id);
	const deleted: string[] = [];
	const failed: string[] = [];
	const models = await listRegisteredModels(user.name);
	for (const m of models) {
		if (await deleteRegisteredModel(m.tag).catch(() => false)) deleted.push(m.tag);
		else failed.push(m.tag);
	}
	// Privacy: remove every log line mentioning the name, its slug, its jobs or its models.
	const logScrub = await scrubLogsAfterPurge({
		name: user.name,
		jobIds: jobs.map((j) => j.id),
		modelTags: models.map((m) => m.tag)
	}).catch(() => ({ files: 0, linesRemoved: 0, perFile: {} }));
	return {
		name: user.name,
		jobsPurged: jobs.length,
		runningOrQueuedCancelled: jobs.filter((j) => ['queued', 'running', 'paused'].includes(j.status)).length,
		ollamaModelsDeleted: deleted,
		ollamaModelsFailed: failed,
		logScrub
	};
}

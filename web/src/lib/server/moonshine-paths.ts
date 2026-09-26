import path from 'node:path';
import { mkdir, readFile, rename, writeFile } from 'node:fs/promises';

/** Shared file locations for the Moonshine v2 backend (same layout as scripts/moonshine_common.py). */
export const DISTILL_HOME = process.env.DISTILL_HOME ?? `${process.env.HOME}/.distill`;
export const JOBS_DIR = path.join(DISTILL_HOME, 'jobs');
export const RESULTS_DIR = path.join(DISTILL_HOME, 'results');
export const QUEUE_DIR = path.join(DISTILL_HOME, 'queue');
export const CONTROL_DIR = path.join(QUEUE_DIR, 'control');
export const USERS_FILE = path.join(DISTILL_HOME, 'users.json');
export const OLLAMA_REGISTRY_FILE = path.join(DISTILL_HOME, 'ollama-models.json');
/** Runtime (APFS-cloned) student folders; launchd jobs cannot read ~/Documents. */
export const STUDENT_MODELS_DIR =
	process.env.MOONSHINE_MLX_MODELS_DIR ?? path.join(DISTILL_HOME, 'models', 'students');
export const OLLAMA_URL = (
	process.env.OLLAMA_URL ?? process.env.DIAGNOSTIC_OLLAMA_URL ?? 'http://127.0.0.1:11434'
).replace(/\/$/, '');

export const JOB_ID_PATTERN = /^[A-Za-z0-9_-]{6,64}$/;

export async function readJsonFile<T>(filePath: string, fallback: T): Promise<T> {
	try {
		return JSON.parse(await readFile(filePath, 'utf-8')) as T;
	} catch {
		return fallback;
	}
}

export async function writeJsonAtomic(filePath: string, data: unknown): Promise<void> {
	await mkdir(path.dirname(filePath), { recursive: true });
	const tmp = `${filePath}.tmp.${process.pid}.${Date.now()}`;
	await writeFile(tmp, JSON.stringify(data, null, 2));
	await rename(tmp, filePath);
}

import { readFile, readdir, mkdir, stat } from 'node:fs/promises';
import path from 'node:path';
import { JOBS_DIR, RESULTS_DIR } from './env';
import { enqueueDag, getQueuePosition, cancelDagRun } from './dagu';
import type { JobParams, JobRecord, JobStatus, JobType, StatusFile, Team } from '../types';

export const DAG_NAME_FOR_TYPE: Record<JobType, string> = {
	'prompt-gen': 'prompt-gen',
	'teacher-gen': 'teacher-gen',
	finetune: 'finetune',
	quantize: 'quantize'
};

interface JobMeta {
	id: string;
	team: Team;
	type: JobType;
	params: JobParams;
	inputFile: string;
	submittedAt: string;
	dagRunId?: string;
}

async function ensureDirs() {
	await mkdir(JOBS_DIR, { recursive: true });
	await mkdir(RESULTS_DIR, { recursive: true });
}

function metaPath(jobId: string): string {
	return path.join(JOBS_DIR, jobId, 'meta.json');
}

function statusPath(jobId: string): string {
	return path.join(RESULTS_DIR, jobId, 'status.json');
}

export async function createJob(meta: JobMeta): Promise<void> {
	await ensureDirs();
	await mkdir(path.join(JOBS_DIR, meta.id), { recursive: true });
	await mkdir(path.join(RESULTS_DIR, meta.id), { recursive: true });
	const fs = await import('node:fs/promises');
	await fs.writeFile(metaPath(meta.id), JSON.stringify(meta, null, 2));
}

export async function submitJob(meta: Omit<JobMeta, 'dagRunId'>): Promise<void> {
	await createJob(meta);
	const dagName = DAG_NAME_FOR_TYPE[meta.type];
	const { dagRunId } = await enqueueDag(dagName, {
		JOB_ID: meta.id,
		TEAM: meta.team,
		INPUT_FILE: meta.inputFile,
		...meta.params
	});
	const fs = await import('node:fs/promises');
	await fs.writeFile(metaPath(meta.id), JSON.stringify({ ...meta, dagRunId }, null, 2));
}

async function readJson<T>(filePath: string): Promise<T | null> {
	try {
		const raw = await readFile(filePath, 'utf-8');
		return JSON.parse(raw) as T;
	} catch {
		return null;
	}
}

async function resolveStatus(jobId: string, meta: JobMeta): Promise<{
	status: JobStatus;
	startedAt?: string;
	completedAt?: string;
	outputPath?: string;
	error?: string;
	queuePosition?: number;
}> {
	const statusFile = await readJson<StatusFile>(statusPath(jobId));
	if (statusFile) {
		return {
			status: statusFile.status === 'complete' ? 'complete' : statusFile.status,
			startedAt: statusFile.started_at,
			completedAt: statusFile.completed_at,
			outputPath: statusFile.output_path,
			error: statusFile.error
		};
	}

	if (meta.dagRunId) {
		const queuePosition = await getQueuePosition(meta.dagRunId);
		if (queuePosition !== null) {
			return { status: 'queued', queuePosition };
		}
	}

	// No status.json yet and not visibly queued: either about to start or Dagu
	// state is unavailable — surface as "running" once picked up, else unknown.
	return { status: meta.dagRunId ? 'running' : 'unknown' };
}

export async function listJobs(): Promise<JobRecord[]> {
	await ensureDirs();
	const ids = await readdir(JOBS_DIR).catch(() => [] as string[]);
	const jobs: JobRecord[] = [];

	for (const id of ids) {
		const meta = await readJson<JobMeta>(metaPath(id));
		if (!meta) continue;
		const resolved = await resolveStatus(id, meta);
		jobs.push({
			id: meta.id,
			team: meta.team,
			type: meta.type,
			status: resolved.status,
			params: meta.params,
			inputFile: meta.inputFile,
			submittedAt: meta.submittedAt,
			startedAt: resolved.startedAt,
			completedAt: resolved.completedAt,
			queuePosition: resolved.queuePosition,
			outputPath: resolved.outputPath,
			error: resolved.error
		});
	}

	jobs.sort((a, b) => b.submittedAt.localeCompare(a.submittedAt));
	return jobs;
}

export async function getJob(jobId: string): Promise<JobRecord | null> {
	const meta = await readJson<JobMeta>(metaPath(jobId));
	if (!meta) return null;
	const resolved = await resolveStatus(jobId, meta);
	return {
		id: meta.id,
		team: meta.team,
		type: meta.type,
		status: resolved.status,
		params: meta.params,
		inputFile: meta.inputFile,
		submittedAt: meta.submittedAt,
		startedAt: resolved.startedAt,
		completedAt: resolved.completedAt,
		queuePosition: resolved.queuePosition,
		outputPath: resolved.outputPath,
		error: resolved.error
	};
}

export async function getJobLogTail(jobId: string, lines = 200): Promise<string> {
	const logPath = path.join(RESULTS_DIR, jobId, 'log.txt');
	try {
		const raw = await readFile(logPath, 'utf-8');
		const allLines = raw.split('\n');
		return allLines.slice(-lines).join('\n');
	} catch {
		return '';
	}
}

export async function cancelJob(jobId: string): Promise<boolean> {
	const meta = await readJson<JobMeta>(metaPath(jobId));
	if (!meta || !meta.dagRunId) return false;
	const dagName = DAG_NAME_FOR_TYPE[meta.type];
	return cancelDagRun(dagName, meta.dagRunId);
}

export interface JobDiagnosis {
	diagnosisMarkdown: string | null;
	suggestedRetryParams: JobParams | null;
}

/** Reads the AI diagnosis written by scripts/diagnose_job.py for a failed job, if any. */
export async function getJobDiagnosis(jobId: string): Promise<JobDiagnosis> {
	const dir = path.join(RESULTS_DIR, jobId);
	const diagnosisMarkdown = await readFile(path.join(dir, 'diagnosis.md'), 'utf-8').catch(
		() => null
	);
	const suggestedRetryParams = await readJson<JobParams>(path.join(dir, 'suggested_retry.json'));
	return { diagnosisMarkdown, suggestedRetryParams };
}

/** Resubmits a job's original input file under a new job id, merging in overridden params. */
export async function retryJobWithParams(
	jobId: string,
	overrideParams: JobParams
): Promise<string> {
	const meta = await readJson<JobMeta>(metaPath(jobId));
	if (!meta) throw new Error(`Job ${jobId} not found`);

	const { nanoid } = await import('nanoid');
	const newId = nanoid(10);
	await submitJob({
		id: newId,
		team: meta.team,
		type: meta.type,
		params: { ...meta.params, ...overrideParams },
		inputFile: meta.inputFile,
		submittedAt: new Date().toISOString()
	});
	return newId;
}

export async function getResultDirSizeBytes(jobId: string): Promise<number> {
	const dir = path.join(RESULTS_DIR, jobId);
	let total = 0;
	try {
		const entries = await readdir(dir, { withFileTypes: true });
		for (const entry of entries) {
			if (entry.isFile()) {
				const s = await stat(path.join(dir, entry.name));
				total += s.size;
			}
		}
	} catch {
		// results dir not created yet
	}
	return total;
}

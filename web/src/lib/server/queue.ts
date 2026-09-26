import path from 'node:path';
import { mkdir, readdir, readFile, stat, writeFile, copyFile } from 'node:fs/promises';
import {
	CONTROL_DIR,
	JOB_ID_PATTERN,
	JOBS_DIR,
	QUEUE_DIR,
	RESULTS_DIR,
	readJsonFile,
	writeJsonAtomic
} from './moonshine-paths';
import { isStudentId, listStudents, type StudentId } from './students';

/**
 * Moonshine v2 job queue (backend for /jobs).
 *
 * The queue runner (scripts/queue_runner.py, launchd com.distill.queue) owns
 * execution: one job at a time, FIFO; while any job is PAUSED the whole queue
 * waits. This module only writes job definitions and control requests, and
 * reads state. status.json is written by the runner only (except the initial
 * "queued" record written at submit time).
 */

import type { JobStatus, JobType } from '../types';
export type JobStatusV2 = JobStatus;
export type JobTypeV2 = JobType;
export type ControlAction = 'pause' | 'resume' | 'cancel' | 'purge';

export const DEFAULT_QUESTION_COUNT = 3000;
export const MAX_QUESTION_COUNT = 20000;
export const MAX_UPLOAD_BYTES = 500 * 1024 * 1024;

/** Advanced overrides accepted for train jobs (all optional; 0/empty = automatic). */
export const TRAIN_ADVANCED_KEYS = ['ITERS', 'EPOCHS', 'LEARNING_RATE', 'BATCH_SIZE', 'NUM_LAYERS', 'MAX_SEQ_LENGTH'] as const;
export type TrainAdvanced = Partial<Record<(typeof TRAIN_ADVANCED_KEYS)[number], number>>;

export interface JobMetaV2 {
	version: 2;
	id: string;
	type: JobTypeV2;
	ownerName: string;
	params: Record<string, string | number>;
	inputFile: string;
	inputName?: string;
	submittedAt: string;
	retryOf?: string;
}

interface StatusFileV2 {
	status: JobStatusV2;
	queued_at?: string;
	started_at?: string;
	last_started_at?: string;
	completed_at?: string;
	paused_at?: string;
	output_path?: string | null;
	error?: string | null;
	attempts?: number;
	priority?: number;
	stopping?: string | null;
	pid?: number | null;
}

export interface JobProgress {
	phase: string | null;
	phaseLabel: string | null;
	phaseIndex: number | null;
	phaseCount: number | null;
	done: number;
	total: number;
	unit: string;
	percent: number | null;
	ratePerSec: number | null;
	/** Seconds left in the current phase (measured speed). */
	etaSec: number | null;
	/** Seconds left for the whole job (train jobs: measured rates for remaining phases). */
	etaTotalSec: number | null;
	updatedAt: string | null;
	extra: Record<string, unknown>;
}

export interface JobV2 {
	id: string;
	type: JobTypeV2;
	ownerName: string;
	status: JobStatusV2;
	params: Record<string, string | number>;
	inputFile: string;
	inputName: string;
	submittedAt: string;
	startedAt?: string;
	completedAt?: string;
	pausedAt?: string;
	attempts: number;
	/** 1 = next to run. Only set for queued jobs. */
	queuePosition?: number;
	/** A pause/resume/cancel the runner has not picked up yet (or is carrying out). */
	pendingAction?: ControlAction | string | null;
	error?: string | null;
	outputPath?: string | null;
	ollamaTag?: string | null;
	progress: JobProgress | null;
}

export interface QueueSummary {
	runnerAlive: boolean;
	runningJobId: string | null;
	pausedJobId: string | null;
	/** Queue blocked because a job is paused. */
	blockedByPause: boolean;
	queuedJobIds: string[];
}

const metaPath = (id: string) => path.join(JOBS_DIR, id, 'meta.json');
const statusPath = (id: string) => path.join(RESULTS_DIR, id, 'status.json');
const progressPath = (id: string) => path.join(RESULTS_DIR, id, 'progress.json');
const controlPath = (id: string) => path.join(CONTROL_DIR, `${id}.json`);

function assertId(id: string) {
	if (!JOB_ID_PATTERN.test(id)) throw new Error('Invalid job id');
}

async function newJobId(): Promise<string> {
	const { nanoid } = await import('nanoid');
	return nanoid(10);
}

function safeName(name: string): string {
	return name.replace(/[^a-zA-Z0-9._-]/g, '_').slice(0, 120) || 'upload';
}

type UploadInput = { name: string; data: Uint8Array } | { name: string; path: string };

async function storeUpload(jobId: string, upload: UploadInput): Promise<{ file: string; name: string }> {
	const dir = path.join(JOBS_DIR, jobId);
	await mkdir(dir, { recursive: true });
	const file = path.join(dir, safeName(upload.name));
	if ('data' in upload) await writeFile(file, upload.data);
	else await copyFile(upload.path, file);
	return { file, name: upload.name };
}

async function createJob(meta: JobMetaV2): Promise<void> {
	await writeJsonAtomic(metaPath(meta.id), meta);
	await mkdir(path.join(RESULTS_DIR, meta.id), { recursive: true });
	await writeJsonAtomic(statusPath(meta.id), { status: 'queued', queued_at: new Date().toISOString() });
}

/** Generate Questions: upload the PROMPT file (.md/.txt) and a count (default 3000). */
export async function submitGenerateJob(opts: {
	ownerName: string;
	promptFile: UploadInput;
	count?: number;
}): Promise<string> {
	const count = Math.round(Number(opts.count ?? DEFAULT_QUESTION_COUNT));
	if (!Number.isFinite(count) || count < 1 || count > MAX_QUESTION_COUNT)
		throw new Error(`Choose between 1 and ${MAX_QUESTION_COUNT} questions.`);
	if (!/\.(md|txt)$/i.test(opts.promptFile.name)) throw new Error('Upload the PROMPT as a .md or .txt file.');
	if ('data' in opts.promptFile) {
		if (opts.promptFile.data.byteLength < 20) throw new Error('The PROMPT file is empty.');
		if (opts.promptFile.data.byteLength > 1024 * 1024) throw new Error('The PROMPT file is too large (1 MB max).');
	}
	const id = await newJobId();
	const up = await storeUpload(id, opts.promptFile);
	await createJob({
		version: 2,
		id,
		type: 'generate',
		ownerName: opts.ownerName,
		params: { COUNT: count },
		inputFile: up.file,
		inputName: up.name,
		submittedAt: new Date().toISOString()
	});
	return id;
}

/** Count valid {"prompt","completion"} lines in an uploaded questions file. */
export function countValidPairs(text: string): { valid: number; invalid: number } {
	let valid = 0;
	let invalid = 0;
	for (const line of text.split('\n')) {
		const s = line.trim();
		if (!s) continue;
		try {
			const o = JSON.parse(s);
			if (o && typeof o.prompt === 'string' && typeof o.completion === 'string' && o.prompt.trim() && o.completion.trim())
				valid++;
			else invalid++;
		} catch {
			invalid++;
		}
	}
	return { valid, invalid };
}

/** Train: chosen student + uploaded questions .jsonl -> split, train, eval, fuse, register in Ollama. */
export async function submitTrainJob(opts: {
	ownerName: string;
	student: StudentId;
	dataFile: UploadInput;
	/** Used in the Ollama tag; defaults to the owner name. */
	modelName?: string;
	advanced?: TrainAdvanced;
}): Promise<string> {
	if (!isStudentId(opts.student)) throw new Error('Choose a student model.');
	const student = (await listStudents()).find((s) => s.id === opts.student);
	if (!student?.available) throw new Error(`${student?.label ?? opts.student} is not installed on the Mac Studio.`);
	if (!/\.jsonl$/i.test(opts.dataFile.name)) throw new Error('Upload the questions as a .jsonl file.');
	const text =
		'data' in opts.dataFile
			? Buffer.from(opts.dataFile.data).toString('utf-8')
			: await readFile(opts.dataFile.path, 'utf-8');
	const { valid } = countValidPairs(text);
	if (valid < 10) throw new Error(`The file needs at least 10 valid {"prompt","completion"} lines (found ${valid}).`);
	const params: Record<string, string | number> = { STUDENT: opts.student };
	if (opts.modelName?.trim()) params.MODEL_NAME = opts.modelName.trim().slice(0, 40);
	for (const key of TRAIN_ADVANCED_KEYS) {
		const v = opts.advanced?.[key];
		if (v !== undefined && v !== null && Number.isFinite(Number(v)) && Number(v) > 0) params[key] = Number(v);
	}
	const id = await newJobId();
	const up = await storeUpload(id, opts.dataFile);
	await createJob({
		version: 2,
		id,
		type: 'train',
		ownerName: opts.ownerName,
		params,
		inputFile: up.file,
		inputName: up.name,
		submittedAt: new Date().toISOString()
	});
	return id;
}

async function readProgress(id: string, status: JobStatusV2): Promise<JobProgress | null> {
	const p = await readJsonFile<Record<string, any> | null>(progressPath(id), null);
	if (!p) return null;
	const phases: string[] = Array.isArray(p.phases) ? p.phases : [];
	const idx = p.phase ? phases.indexOf(p.phase) : -1;
	const done = Number(p.done ?? 0);
	const total = Number(p.total ?? 0);
	const active = status === 'running';
	return {
		phase: p.phase ?? null,
		phaseLabel: p.phase_label ?? p.phase ?? null,
		phaseIndex: idx >= 0 ? idx + 1 : null,
		phaseCount: phases.length || null,
		done,
		total,
		unit: p.unit ?? 'items',
		percent: total > 0 ? Math.min(100, Math.round((done / total) * 1000) / 10) : null,
		ratePerSec: p.rate_per_sec ?? null,
		etaSec: active ? (p.eta_sec ?? null) : null,
		etaTotalSec: active ? (p.eta_total_sec ?? p.eta_sec ?? null) : null,
		updatedAt: p.updated_at ?? null,
		extra: p.extra ?? {}
	};
}

async function readJob(id: string, queued?: string[]): Promise<JobV2 | null> {
	const meta = await readJsonFile<JobMetaV2 | null>(metaPath(id), null);
	if (!meta || meta.version !== 2) return null;
	const st = await readJsonFile<StatusFileV2>(statusPath(id), { status: 'queued' });
	const control = await readJsonFile<{ action?: string } | null>(controlPath(id), null);
	const state = await readJsonFile<{ ollama_tag?: string } | null>(path.join(RESULTS_DIR, id, 'state.json'), null);
	const pos = queued ? queued.indexOf(id) : -1;
	return {
		id,
		type: meta.type,
		ownerName: meta.ownerName,
		status: st.status,
		params: meta.params,
		inputFile: meta.inputFile,
		inputName: meta.inputName ?? path.basename(meta.inputFile),
		submittedAt: meta.submittedAt,
		startedAt: st.started_at,
		completedAt: st.completed_at,
		pausedAt: st.status === 'paused' ? st.paused_at : undefined,
		attempts: st.attempts ?? 0,
		queuePosition: st.status === 'queued' && pos >= 0 ? pos + 1 : undefined,
		pendingAction: control?.action ?? st.stopping ?? null,
		error: st.status === 'failed' ? st.error : null,
		outputPath: st.output_path ?? null,
		ollamaTag: state?.ollama_tag ?? null,
		progress: await readProgress(id, st.status)
	};
}

async function allJobIds(): Promise<string[]> {
	return readdir(JOBS_DIR).catch(() => [] as string[]);
}

/** Queued job ids in run order (same ordering as the runner: resumed first, then FIFO). */
async function queuedOrder(): Promise<string[]> {
	const rows: { id: string; priority: number; submittedAt: string }[] = [];
	for (const id of await allJobIds()) {
		const meta = await readJsonFile<JobMetaV2 | null>(metaPath(id), null);
		if (!meta || meta.version !== 2) continue;
		const st = await readJsonFile<StatusFileV2 | null>(statusPath(id), null);
		if (st?.status === 'queued') rows.push({ id, priority: st.priority ?? 0, submittedAt: meta.submittedAt });
	}
	rows.sort((a, b) => b.priority - a.priority || a.submittedAt.localeCompare(b.submittedAt));
	return rows.map((r) => r.id);
}

/** All v2 jobs, newest first. Pass ownerName to filter to one person. */
export async function listJobs(filter: { ownerName?: string } = {}): Promise<JobV2[]> {
	const queued = await queuedOrder();
	const jobs: JobV2[] = [];
	for (const id of await allJobIds()) {
		const job = await readJob(id, queued);
		if (!job) continue;
		if (filter.ownerName !== undefined && job.ownerName !== filter.ownerName) continue;
		jobs.push(job);
	}
	jobs.sort((a, b) => b.submittedAt.localeCompare(a.submittedAt));
	return jobs;
}

export async function getJob(id: string): Promise<JobV2 | null> {
	if (!JOB_ID_PATTERN.test(id)) return null;
	return readJob(id, await queuedOrder());
}

export async function getQueueSummary(): Promise<QueueSummary> {
	const jobs = await listJobs();
	let runnerAlive = false;
	try {
		const hb = await stat(path.join(QUEUE_DIR, 'runner-heartbeat'));
		runnerAlive = Date.now() - hb.mtimeMs < 20_000;
	} catch {
		/* no heartbeat */
	}
	const paused = jobs.find((j) => j.status === 'paused');
	return {
		runnerAlive,
		runningJobId: jobs.find((j) => j.status === 'running')?.id ?? null,
		pausedJobId: paused?.id ?? null,
		blockedByPause: Boolean(paused),
		queuedJobIds: await queuedOrder()
	};
}

async function sendControl(id: string, action: ControlAction): Promise<void> {
	await writeJsonAtomic(controlPath(id), { action, at: new Date().toISOString() });
}

export type ControlResult = { ok: true } | { ok: false; error: string };

/** PAUSE: stops the running job at a safe point; the whole queue waits until resume/cancel. */
export async function pauseJob(id: string): Promise<ControlResult> {
	const job = await getJob(id);
	if (!job) return { ok: false, error: 'Job not found.' };
	if (job.status !== 'running') return { ok: false, error: 'Only a running job can be paused.' };
	await sendControl(id, 'pause');
	return { ok: true };
}

/** RESUME: a paused job (or a failed one, retried in place) continues from saved progress. */
export async function resumeJob(id: string): Promise<ControlResult> {
	const job = await getJob(id);
	if (!job) return { ok: false, error: 'Job not found.' };
	if (job.status !== 'paused' && job.status !== 'failed')
		return { ok: false, error: 'Only a paused or failed job can be resumed.' };
	await sendControl(id, 'resume');
	return { ok: true };
}

/** CANCEL: stops the job (if running) and removes it from the queue. Files are kept. */
export async function cancelJob(id: string): Promise<ControlResult> {
	const job = await getJob(id);
	if (!job) return { ok: false, error: 'Job not found.' };
	if (job.status === 'completed' || job.status === 'cancelled')
		return { ok: false, error: `The job is already ${job.status}.` };
	await sendControl(id, 'cancel');
	return { ok: true };
}

/** Cancel + delete all files + delete Ollama models the job registered (runner does the work). */
export async function purgeJob(id: string): Promise<void> {
	assertId(id);
	await sendControl(id, 'purge');
}

/**
 * Retry a failed job. Without overrides it resumes in place from saved
 * progress. With overrides (e.g. from the AI diagnosis) it submits a new job
 * with the same input and merged params.
 */
export async function retryJob(
	id: string,
	ownerName: string,
	overrides: Record<string, string | number> = {}
): Promise<{ jobId: string }> {
	const meta = await readJsonFile<JobMetaV2 | null>(metaPath(id), null);
	if (!meta || meta.version !== 2) throw new Error('Job not found');
	if (Object.keys(overrides).length === 0) {
		const r = await resumeJob(id);
		if (!r.ok) throw new Error(r.error);
		return { jobId: id };
	}
	const newId = await newJobId();
	const up = await storeUpload(newId, { name: meta.inputName ?? path.basename(meta.inputFile), path: meta.inputFile });
	await createJob({
		...meta,
		id: newId,
		ownerName,
		params: { ...meta.params, ...overrides },
		inputFile: up.file,
		submittedAt: new Date().toISOString(),
		retryOf: id
	});
	return { jobId: newId };
}

export async function getJobLogTail(id: string, lines = 200): Promise<string> {
	assertId(id);
	try {
		const raw = await readFile(path.join(RESULTS_DIR, id, 'log.txt'), 'utf-8');
		return raw.split('\n').slice(-lines).join('\n');
	} catch {
		return '';
	}
}

/** AI diagnosis (scripts/diagnose_job.py) for a failed job, if any. */
export async function getJobDiagnosis(id: string): Promise<{
	diagnosisMarkdown: string | null;
	suggestedRetryParams: Record<string, string | number> | null;
}> {
	assertId(id);
	const dir = path.join(RESULTS_DIR, id);
	return {
		diagnosisMarkdown: await readFile(path.join(dir, 'diagnosis.md'), 'utf-8').catch(() => null),
		suggestedRetryParams: await readJsonFile(path.join(dir, 'suggested_retry.json'), null)
	};
}

export interface EvalReport {
	json: Record<string, any>;
	markdown: string;
}

export async function getEvalReport(id: string): Promise<EvalReport | null> {
	assertId(id);
	const dir = path.join(RESULTS_DIR, id);
	const json = await readJsonFile<Record<string, any> | null>(path.join(dir, 'eval_report.json'), null);
	if (!json) return null;
	return { json, markdown: await readFile(path.join(dir, 'eval_report.md'), 'utf-8').catch(() => '') };
}

/** Training loss curve [(step, loss)] with absolute step numbers across resumes. */
export async function getTrainingCurve(id: string): Promise<{
	train: [number, number][];
	valid: [number, number][];
	totalSteps: number | null;
}> {
	assertId(id);
	const s = await readJsonFile<Record<string, any>>(path.join(RESULTS_DIR, id, 'state.json'), {});
	return { train: s.train_losses ?? [], valid: s.val_losses ?? [], totalSteps: s.iters_total ?? null };
}

export interface JobArtifact {
	key: string;
	label: string;
	/** Path relative to results/<id>; pass to downloads.validateResultPath(jobId, relPath). */
	relPath: string;
	kind: 'file' | 'dir';
}

/** Downloadable results that exist right now. */
export async function listJobArtifacts(id: string): Promise<JobArtifact[]> {
	assertId(id);
	const dir = path.join(RESULTS_DIR, id);
	const candidates: JobArtifact[] = [
		{ key: 'questions', label: 'Questions (.jsonl)', relPath: 'output.jsonl', kind: 'file' },
		{ key: 'report-md', label: 'Evaluation report (.md)', relPath: 'eval_report.md', kind: 'file' },
		{ key: 'report-json', label: 'Evaluation report (.json)', relPath: 'eval_report.json', kind: 'file' },
		{ key: 'adapters', label: 'LoRA adapters', relPath: 'adapters', kind: 'dir' },
		{ key: 'fused', label: 'Fused 4-bit MLX model', relPath: 'fused-model', kind: 'dir' },
		{ key: 'log', label: 'Log', relPath: 'log.txt', kind: 'file' }
	];
	const out: JobArtifact[] = [];
	for (const c of candidates) {
		const s = await stat(path.join(dir, c.relPath)).catch(() => null);
		if (s && (c.kind === 'dir' ? s.isDirectory() : s.isFile())) out.push(c);
	}
	return out;
}

export function formatEta(sec: number | null | undefined): string {
	if (sec === null || sec === undefined || !Number.isFinite(sec)) return '';
	const s = Math.max(0, Math.round(sec));
	const h = Math.floor(s / 3600);
	const m = Math.floor((s % 3600) / 60);
	if (h > 0) return `about ${h} h ${m} min left`;
	if (m > 0) return `about ${m} min left`;
	return 'less than a minute left';
}

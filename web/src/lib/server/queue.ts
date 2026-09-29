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
/** Source documents for grounded Generate Questions (the Teacher reads them; the Student never sees them). */
export const MAX_SOURCE_DOCS = 20;
export const MAX_SOURCE_DOC_BYTES = 100 * 1024 * 1024;
export const MAX_SOURCE_TOTAL_BYTES = 250 * 1024 * 1024;
export const SOURCE_DOC_PATTERN = /\.(pdf|txt|md|markdown)$/i;
export { DOC_ROLES, DEFAULT_DOC_ROLE, docRole, type DocRole } from '../types';
import { docRole, type DocRole } from '../types';

/** Advanced overrides accepted for train jobs (all optional; 0/empty = automatic). */
export const TRAIN_ADVANCED_KEYS = ['ITERS', 'EPOCHS', 'LEARNING_RATE', 'BATCH_SIZE', 'NUM_LAYERS', 'MAX_SEQ_LENGTH', 'SAVE_EVERY'] as const;
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
	/** Generate jobs: uploaded source documents (grounded generation). */
	docs?: { file: string; name: string; bytes?: number; role?: DocRole }[];
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
	/** Generate jobs with source documents: original file names. */
	docNames: string[];
	/** Role of each document in docNames (main / supporting / background). */
	docRoles: DocRole[];
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
type DocInput = UploadInput & { role?: DocRole };

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

async function storeDocs(
	jobId: string,
	docs: DocInput[]
): Promise<{ file: string; name: string; bytes: number; role: DocRole }[]> {
	const dir = path.join(JOBS_DIR, jobId, 'docs');
	await mkdir(dir, { recursive: true });
	const used = new Set<string>();
	const out: { file: string; name: string; bytes: number; role: DocRole }[] = [];
	for (const d of docs) {
		let base = safeName(d.name);
		const ext = path.extname(base);
		for (let k = 2; used.has(base.toLowerCase()); k++) base = `${path.basename(safeName(d.name), ext)}-${k}${ext}`;
		used.add(base.toLowerCase());
		const file = path.join(dir, base);
		if ('data' in d) await writeFile(file, d.data);
		else await copyFile(d.path, file);
		out.push({ file, name: d.name, bytes: (await stat(file)).size, role: docRole(d.role) });
	}
	return out;
}

/** Validate source documents (PDF / .txt / .md) before a grounded job is created. */
export function validateSourceDocs(docs: { name: string; size: number }[]): void {
	if (docs.length > MAX_SOURCE_DOCS) throw new Error(`Upload at most ${MAX_SOURCE_DOCS} source documents.`);
	let total = 0;
	for (const d of docs) {
		if (!SOURCE_DOC_PATTERN.test(d.name)) throw new Error(`${d.name}: source documents must be PDF, .txt or .md files.`);
		if (d.size > MAX_SOURCE_DOC_BYTES) throw new Error(`${d.name} is larger than 100 MB.`);
		total += d.size;
	}
	if (total > MAX_SOURCE_TOTAL_BYTES) throw new Error('The source documents add up to more than 250 MB.');
}

/** Generate Questions: upload the PROMPT file (.md/.txt), optional source documents, and a count (default 3000). */
export async function submitGenerateJob(opts: {
	ownerName: string;
	promptFile: UploadInput;
	count?: number;
	docs?: { name: string; data: Uint8Array; role?: DocRole }[];
}): Promise<string> {
	const count = Math.round(Number(opts.count ?? DEFAULT_QUESTION_COUNT));
	if (!Number.isFinite(count) || count < 1 || count > MAX_QUESTION_COUNT)
		throw new Error(`Choose between 1 and ${MAX_QUESTION_COUNT} questions.`);
	if (!/\.(md|txt)$/i.test(opts.promptFile.name)) throw new Error('Upload the PROMPT as a .md or .txt file.');
	if ('data' in opts.promptFile) {
		if (opts.promptFile.data.byteLength < 20) throw new Error('The PROMPT file is empty.');
		if (opts.promptFile.data.byteLength > 1024 * 1024) throw new Error('The PROMPT file is too large (1 MB max).');
	}
	const docs = opts.docs ?? [];
	validateSourceDocs(docs.map((d) => ({ name: d.name, size: d.data.byteLength })));
	const id = await newJobId();
	const up = await storeUpload(id, opts.promptFile);
	const storedDocs = docs.length ? await storeDocs(id, docs) : [];
	const params: Record<string, string | number> = { COUNT: count };
	if (storedDocs.length) params.GROUNDED = 1;
	await createJob({
		version: 2,
		id,
		type: 'generate',
		ownerName: opts.ownerName,
		params,
		inputFile: up.file,
		inputName: up.name,
		submittedAt: new Date().toISOString(),
		...(storedDocs.length ? { docs: storedDocs } : {})
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
		progress: await readProgress(id, st.status),
		docNames: Array.isArray(meta.docs) ? meta.docs.map((d) => d.name) : [],
		docRoles: Array.isArray(meta.docs) ? meta.docs.map((d) => docRole(d.role)) : []
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

/** Pause / resume / cancel are for the job's owner only (the session's selected name must equal meta.ownerName). */
export function isJobOwner(job: Pick<JobV2, 'ownerName'> | null | undefined, userName: string | null | undefined): boolean {
	return Boolean(job && userName && job.ownerName === userName);
}

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
	const docs = meta.docs?.length
		? await storeDocs(newId, meta.docs.map((d) => ({ name: d.name, path: d.file, role: docRole(d.role) })))
		: undefined;
	await createJob({
		...meta,
		...(docs ? { docs } : {}),
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

export interface JobAdvice {
	markdown: string | null;
	error: string | null;
	teacher: string | null;
	nWrong: number | null;
	nSampled: number | null;
	nEval: number | null;
	seconds: number | null;
	createdAt: string | null;
}

/** Teacher's analysis of the held-out questions the trained Student missed (results/<id>/advice.json). */
export async function getJobAdvice(id: string): Promise<JobAdvice | null> {
	assertId(id);
	const a = await readJsonFile<Record<string, any> | null>(path.join(RESULTS_DIR, id, 'advice.json'), null);
	if (!a) return null;
	return {
		markdown: typeof a.markdown === 'string' && a.markdown.trim() ? a.markdown : null,
		error: a.error ?? null,
		teacher: a.teacher ?? null,
		nWrong: a.n_wrong ?? null,
		nSampled: a.n_sampled ?? null,
		nEval: a.n_eval ?? null,
		seconds: a.seconds ?? null,
		createdAt: a.created_at ?? null
	};
}

export interface BestCheckpoint {
	step: number;
	valLoss: number | null;
	finalStep: number;
	finalValLoss: number | null;
	used: 'best' | 'final';
	saveEvery: number | null;
	checkpointsScored: number | null;
	valItems: number | null;
}

export interface TrainSettings {
	iters: number | null;
	epochs: number | null;
	epochsDefault: number | null;
	epochsUser: boolean;
	itersUser: boolean;
	batchSize: number | null;
	saveEvery: number | null;
}

/** Training loss curve [(step, loss)] with absolute step numbers across resumes, plus the checkpoint picked. */
export async function getTrainingCurve(id: string): Promise<{
	train: [number, number][];
	valid: [number, number][];
	totalSteps: number | null;
	best: BestCheckpoint | null;
	settings: TrainSettings | null;
}> {
	assertId(id);
	const s = await readJsonFile<Record<string, any>>(path.join(RESULTS_DIR, id, 'state.json'), {});
	const b = s.best_checkpoint;
	const c = s.train_cfg;
	return {
		train: s.train_losses ?? [],
		valid: s.val_losses ?? [],
		totalSteps: s.iters_total ?? null,
		best: b
			? {
					step: b.step,
					valLoss: b.val_loss ?? null,
					finalStep: b.final_step,
					finalValLoss: b.final_val_loss ?? null,
					used: b.used === 'best' ? 'best' : 'final',
					saveEvery: b.save_every ?? null,
					checkpointsScored: b.checkpoints_scored ?? null,
					valItems: b.val_items ?? null
				}
			: null,
		settings: c
			? {
					iters: c.iters ?? null,
					epochs: c.epochs ?? null,
					epochsDefault: c.epochs_default ?? null,
					epochsUser: Boolean(c.epochs_user),
					itersUser: Boolean(c.iters_user),
					batchSize: c.batch_size ?? null,
					saveEvery: c.save_every ?? null
				}
			: null
	};
}

export interface GenerateCheck {
	checked: number;
	kept: number;
	dropped: number;
	replaced: number;
	dropRate: number | null;
	reasons: Record<string, number>;
	capHit: boolean;
	attempts: number | null;
	maxAttempts: number | null;
	nearExactDropped: number;
	warning: string | null;
	checkSec: number | null;
	genSec: number | null;
	/** Kept refuse-and-refer answers ("I don't have that information; ask your counselor") and how many were checked. */
	refusalsKept: number;
	refusalsChecked: number;
	/** Kept after a support check although the short re-solve could not find the answer. */
	supportKept: number;
}

export interface GenerateVariety {
	items: number;
	nearDuplicates: number;
	rate: number;
	threshold: number;
	warn: boolean;
	examples: { i: number; similar_to: number; jaccard: number; prompt: string; other: string }[];
}

/** Answer-check and variety stats of a Generate Questions job (check.json / variety.json, or live progress). */
export async function getGenerateQuality(
	id: string,
	progressExtra: Record<string, unknown> = {}
): Promise<{ check: GenerateCheck | null; variety: GenerateVariety | null }> {
	assertId(id);
	const dir = path.join(RESULTS_DIR, id);
	const c =
		(await readJsonFile<Record<string, any> | null>(path.join(dir, 'check.json'), null)) ??
		((progressExtra.check as Record<string, any> | undefined) || null);
	const v = await readJsonFile<Record<string, any> | null>(path.join(dir, 'variety.json'), null);
	return {
		check: c
			? {
					checked: c.checked ?? 0,
					kept: c.kept ?? 0,
					dropped: c.dropped ?? 0,
					replaced: c.replaced ?? 0,
					dropRate: c.drop_rate ?? null,
					reasons: c.reasons ?? {},
					capHit: Boolean(c.cap_hit),
					attempts: c.attempts ?? null,
					maxAttempts: c.max_attempts ?? null,
					nearExactDropped: c.near_exact_dropped ?? 0,
					warning: c.warning ?? null,
					checkSec: c.check_sec ?? null,
					genSec: c.gen_sec ?? null,
					refusalsKept: c.refusals_kept ?? 0,
					refusalsChecked: c.refusals_checked ?? 0,
					supportKept: c.support_kept ?? 0
				}
			: null,
		variety: v
			? {
					items: v.items ?? 0,
					nearDuplicates: v.near_duplicates ?? 0,
					rate: v.rate ?? 0,
					threshold: v.threshold ?? 0.8,
					warn: Boolean(v.warn),
					examples: (v.examples ?? []).slice(0, 3)
				}
			: null
	};
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
		{ key: 'sources', label: 'Sources (.jsonl)', relPath: 'sources.jsonl', kind: 'file' },
		{ key: 'chunks', label: 'Document sections (.jsonl)', relPath: 'chunks.jsonl', kind: 'file' },
		{ key: 'rejected', label: 'Dropped by the answer check (.jsonl)', relPath: 'rejected.jsonl', kind: 'file' },
		{ key: 'report-md', label: 'Evaluation report (.md)', relPath: 'eval_report.md', kind: 'file' },
		{ key: 'report-json', label: 'Evaluation report (.json)', relPath: 'eval_report.json', kind: 'file' },
		{ key: 'adapters', label: 'LoRA adapters', relPath: 'adapters', kind: 'dir' },
		{ key: 'fused', label: 'Fused 4-bit MLX model', relPath: 'fused-model', kind: 'dir' },
		{ key: 'ollama-gguf', label: 'Download for Ollama (GGUF, Mac/Windows/Linux)', relPath: 'ollama-gguf.zip', kind: 'file' },
		{ key: 'log', label: 'Log', relPath: 'log.txt', kind: 'file' }
	];
	const out: JobArtifact[] = [];
	for (const c of candidates) {
		const s = await stat(path.join(dir, c.relPath)).catch(() => null);
		if (s && (c.kind === 'dir' ? s.isDirectory() : s.isFile())) out.push(c);
	}
	return out;
}

export interface GgufStatus {
	state: 'none' | 'queued' | 'running' | 'done' | 'failed';
	step?: string | null;
	message?: string | null;
	error?: string | null;
	bytes?: number | null;
	seconds?: number | null;
	modelName?: string | null;
	updatedAt?: string | null;
}

const GGUF_REQ_DIR = path.join(QUEUE_DIR, 'gguf-requests');

function pidAlive(pid: unknown): boolean {
	if (typeof pid !== 'number' || pid <= 0) return false;
	try {
		process.kill(pid, 0);
		return true;
	} catch (e) {
		return (e as NodeJS.ErrnoException).code === 'EPERM';
	}
}

/** State of the portable Ollama (GGUF) package for a train job (scripts/export_gguf.py). */
export async function getGgufStatus(id: string): Promise<GgufStatus> {
	assertId(id);
	const dir = path.join(RESULTS_DIR, id);
	const zip = await stat(path.join(dir, 'ollama-gguf.zip')).catch(() => null);
	const s = await readJsonFile<Record<string, any> | null>(path.join(dir, 'gguf_status.json'), null);
	const pending = await stat(path.join(GGUF_REQ_DIR, `${id}.json`)).catch(() => null);
	const base = {
		step: s?.step ?? null,
		message: s?.message ?? null,
		error: s?.error ?? null,
		bytes: zip?.size ?? s?.bytes ?? null,
		seconds: s?.seconds ?? null,
		modelName: s?.model_name ?? null,
		updatedAt: s?.updated_at ?? null
	};
	if (s?.state === 'running' && pidAlive(s.pid)) return { ...base, state: 'running' };
	if (pending) return { ...base, state: 'queued', message: 'Waiting to start' };
	if (s?.state === 'queued' && !zip) {
		// the runner picks requests up within seconds; a stale "queued" means it never started
		const age = Date.now() - Date.parse(s.updated_at ?? '');
		if (!(age > 120_000)) return { ...base, state: 'queued', message: 'Starting' };
		return { ...base, state: 'failed', error: 'The build did not start. Is the queue runner running?' };
	}
	if (zip) return { ...base, state: 'done', error: null };
	if (s?.state === 'failed') return { ...base, state: 'failed' };
	if (s?.state === 'running') return { ...base, state: 'failed', error: s?.error ?? 'The build stopped unexpectedly.' };
	return { ...base, state: 'none' };
}

/** Ask the queue runner to build ollama-gguf.zip for a finished train job (runs in the background). */
export async function requestGgufExport(id: string): Promise<GgufStatus> {
	assertId(id);
	const job = await getJob(id);
	if (!job) throw new Error('Job not found');
	if (job.type !== 'train' || job.status !== 'completed') throw new Error('Only finished train jobs can be exported.');
	const cur = await getGgufStatus(id);
	if (cur.state === 'queued' || cur.state === 'running') return cur;
	const dir = path.join(RESULTS_DIR, id);
	await writeJsonAtomic(path.join(dir, 'gguf_status.json'), {
		state: 'queued',
		step: 'queued',
		message: 'Waiting to start',
		error: null,
		requested_at: new Date().toISOString(),
		updated_at: new Date().toISOString()
	});
	await writeJsonAtomic(path.join(GGUF_REQ_DIR, `${id}.json`), { action: 'gguf', at: new Date().toISOString() });
	return getGgufStatus(id);
}

export interface JobDocuments {
	docs: { name: string; pages?: number | null; words?: number; chunks?: number; method?: string; error?: string }[];
	chunks: number;
	words: number;
	/** Sections used so far (from sources.jsonl). */
	covered: number | null;
	/** Questions kept per document (by the main section of the batch each question came from). */
	questionsByDoc: Record<string, number>;
}

async function readChunkDocs(dir: string): Promise<string[]> {
	const out: string[] = [];
	try {
		for (const line of (await readFile(path.join(dir, 'chunks.jsonl'), 'utf-8')).split('\n')) {
			if (!line.trim()) continue;
			try {
				const c = JSON.parse(line);
				out[Number(c.id ?? out.length)] = c.doc;
			} catch {
				/* skip */
			}
		}
	} catch {
		/* none */
	}
	return out;
}

/** Source documents of a grounded generate job, after the job has extracted them. */
export async function getJobDocuments(id: string): Promise<JobDocuments | null> {
	assertId(id);
	const dir = path.join(RESULTS_DIR, id);
	const d = await readJsonFile<JobDocuments | null>(path.join(dir, 'documents.json'), null);
	if (!d) return null;
	let covered: number | null = null;
	const questionsByDoc: Record<string, number> = {};
	try {
		const seen = new Set<number>();
		let chunkDoc: string[] | null = null; // older jobs: sources.jsonl lines have no "doc"; map the first chunk
		for (const line of (await readFile(path.join(dir, 'sources.jsonl'), 'utf-8')).split('\n')) {
			if (!line.trim()) continue;
			try {
				const o = JSON.parse(line);
				const ids: number[] = (o.chunks ?? []).map(Number);
				for (const c of ids) seen.add(c);
				let doc: string | undefined = o.doc;
				if (!doc && ids.length) {
					chunkDoc ??= await readChunkDocs(dir);
					doc = chunkDoc[ids[0]];
				}
				if (doc) questionsByDoc[doc] = (questionsByDoc[doc] ?? 0) + 1;
			} catch {
				/* skip */
			}
		}
		covered = seen.size;
	} catch {
		/* not started */
	}
	return { docs: d.docs ?? [], chunks: d.chunks ?? 0, words: d.words ?? 0, covered, questionsByDoc };
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

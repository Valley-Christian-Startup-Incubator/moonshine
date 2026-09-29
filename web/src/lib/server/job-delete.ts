import path from 'node:path';
import { existsSync } from 'node:fs';
import { JOBS_DIR, RESULTS_DIR } from './moonshine-paths';
import { getJob, isJobOwner, purgeJob } from './queue';
import { deleteRegisteredModel, listRegisteredModels } from './ollama';
import { scrubLogsAfterPurge, type ScrubResult } from './log-scrub';

/** Only jobs in these states can be deleted; a queued, running or paused job must be cancelled first. */
export const DELETABLE_STATUSES = ['completed', 'failed', 'cancelled'] as const;

export type DeleteJobResult =
	| { ok: true; id: string; ollamaModelsDeleted: string[]; ollamaModelsFailed: string[]; filesRemoved: boolean; logScrub: ScrubResult }
	| { ok: false; status: 403 | 404 | 409; error: string };

/**
 * Owner-only delete of ONE finished job. Uses the same purge path as the admin "delete a name" action: the queue
 * runner deletes the job dir, the results dir, the job's Ollama model(s) and their registry entries; then this
 * also deletes any registered model of the job the runner missed and scrubs the job ID and model tags from the
 * logs (log-scrub.ts). Base models are never touched.
 */
export async function deleteOwnJob(id: string, userName: string | null | undefined): Promise<DeleteJobResult> {
	const job = await getJob(id);
	if (!job) return { ok: false, status: 404, error: 'Job not found.' };
	if (!isJobOwner(job, userName)) return { ok: false, status: 403, error: `Only ${job.ownerName} can delete this job.` };
	return purgeFinishedJob(id);
}

/** Admin (hidden /admin page, admin cookie checked by the route): delete any one non-running job, whoever owns it. */
export async function adminDeleteJob(id: string): Promise<DeleteJobResult> {
	return purgeFinishedJob(id);
}

async function purgeFinishedJob(id: string): Promise<DeleteJobResult> {
	const job = await getJob(id);
	if (!job) return { ok: false, status: 404, error: 'Job not found.' };
	if (!(DELETABLE_STATUSES as readonly string[]).includes(job.status) || job.pendingAction)
		return { ok: false, status: 409, error: 'Only finished, failed or cancelled jobs can be deleted. Cancel this job first.' };
	const models = (await listRegisteredModels()).filter((m) => m.jobId === id);
	const tags = [...new Set([...models.map((m) => m.tag), ...(job.ollamaTag ? [job.ollamaTag] : [])])];
	await purgeJob(id);
	const logScrub = await scrubLogsAfterPurge({ name: '', jobIds: [id], modelTags: tags }).catch(() => ({
		files: 0,
		linesRemoved: 0,
		perFile: {}
	}));
	// The runner deletes the job's models; anything still registered to this job is deleted here.
	const deleted: string[] = [];
	const failed: string[] = [];
	const left = new Set((await listRegisteredModels()).filter((m) => m.jobId === id).map((m) => m.tag));
	for (const tag of tags) {
		if (!left.has(tag)) {
			deleted.push(tag);
			continue;
		}
		if (await deleteRegisteredModel(tag).catch(() => false)) deleted.push(tag);
		else failed.push(tag);
	}
	const filesRemoved = !existsSync(path.join(JOBS_DIR, id)) && !existsSync(path.join(RESULTS_DIR, id));
	return { ok: true, id, ollamaModelsDeleted: deleted, ollamaModelsFailed: failed, filesRemoved, logScrub };
}

import { open, realpath } from 'node:fs/promises';
import path from 'node:path';
import { JOBS_DIR } from '$lib/server/env';
import { error, fail, redirect } from '@sveltejs/kit';
import type { Actions, PageServerLoad } from './$types';
import { getJob, getJobLogTail, getJobDiagnosis, retryJobWithParams } from '$lib/server/jobs';

// Read only a bounded prefix; never load a training dataset just for animation.
async function inputExcerpts(inputFile: string): Promise<string[]> {
	if (!inputFile) return [];
	let file;
	try {
		const root = await realpath(JOBS_DIR);
		const source = await realpath(inputFile);
		const relative = path.relative(root, source);
		if (relative.startsWith('..') || path.isAbsolute(relative)) return [];
		file = await open(source, 'r');
		if (!(await file.stat()).isFile()) return [];
		const buffer = Buffer.alloc(16_384);
		const { bytesRead } = await file.read(buffer, 0, buffer.length, 0);
		const excerpts: string[] = [];
		for (const line of buffer.subarray(0, bytesRead).toString('utf8').split('\n')) {
			try {
				const row = JSON.parse(line);
				const text = row?.prompt ?? row?.generation_prompt ?? row?.topic ?? row?.text;
				if (typeof text === 'string' && text.trim()) excerpts.push(text.replace(/\s+/g, ' ').trim().slice(0, 90));
			} catch { /* The prefix may end partway through a JSONL record. */ }
			if (excerpts.length === 4) break;
		}
		return excerpts;
	} catch { return []; }
	finally { await file?.close(); }
}

export const load: PageServerLoad = async ({ params }) => {
	const job = await getJob(params.id);
	if (!job) error(404, 'Job not found');
	const log = job.status === 'running' || job.status === 'failed' ? await getJobLogTail(job.id) : '';
	const diagnosis =
		job.status === 'failed'
			? await getJobDiagnosis(job.id)
			: { diagnosisMarkdown: null, suggestedRetryParams: null };
	const excerpts = job.status === 'running' ? await inputExcerpts(job.inputFile) : [];
	return { job, log, diagnosis, excerpts };
};

export const actions: Actions = {
	retry: async ({ params, locals }) => {
		if (!locals.user) return fail(401, { error: 'Please sign in before retrying a job.' });
		const job = await getJob(params.id);
		if (!job) error(404, 'Job not found');
		const { suggestedRetryParams } = await getJobDiagnosis(params.id);
		if (!suggestedRetryParams || Object.keys(suggestedRetryParams).length === 0) {
			return fail(400, { error: 'No suggested retry params available for this job.' });
		}

		let newId: string;
		try {
			newId = await retryJobWithParams(params.id, suggestedRetryParams, locals.user);
		} catch (err) {
			return fail(502, {
				error: `Failed to enqueue retry: ${err instanceof Error ? err.message : String(err)}`
			});
		}

		redirect(303, `/jobs/${newId}?submitted=1`);
	}
};

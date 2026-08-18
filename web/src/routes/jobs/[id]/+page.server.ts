import { error, fail, redirect } from '@sveltejs/kit';
import type { Actions, PageServerLoad } from './$types';
import { getJob, getJobLogTail, getJobDiagnosis, retryJobWithParams } from '$lib/server/jobs';

export const load: PageServerLoad = async ({ params }) => {
	const job = await getJob(params.id);
	if (!job) error(404, 'Job not found');
	const log = job.status === 'running' || job.status === 'failed' ? await getJobLogTail(job.id) : '';
	const diagnosis =
		job.status === 'failed'
			? await getJobDiagnosis(job.id)
			: { diagnosisMarkdown: null, suggestedRetryParams: null };
	return { job, log, diagnosis };
};

export const actions: Actions = {
	retry: async ({ params }) => {
		const job = await getJob(params.id);
		if (!job) error(404, 'Job not found');
		const { suggestedRetryParams } = await getJobDiagnosis(params.id);
		if (!suggestedRetryParams || Object.keys(suggestedRetryParams).length === 0) {
			return fail(400, { error: 'No suggested retry params available for this job.' });
		}

		let newId: string;
		try {
			newId = await retryJobWithParams(params.id, suggestedRetryParams);
		} catch (err) {
			return fail(502, {
				error: `Failed to enqueue retry: ${err instanceof Error ? err.message : String(err)}`
			});
		}

		redirect(303, `/jobs/${newId}?submitted=1`);
	}
};

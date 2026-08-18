import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';
import { getJob, getJobLogTail } from '$lib/server/jobs';

export const load: PageServerLoad = async ({ params }) => {
	const job = await getJob(params.id);
	if (!job) error(404, 'Job not found');
	const log = job.status === 'running' || job.status === 'failed' ? await getJobLogTail(job.id) : '';
	return { job, log };
};

import type { PageServerLoad } from './$types';
import { getQueueSummary, listJobs } from '$lib/server/queue';

export const load: PageServerLoad = async () => {
	const [jobs, queue] = await Promise.all([listJobs(), getQueueSummary()]);
	return { jobs, queue };
};

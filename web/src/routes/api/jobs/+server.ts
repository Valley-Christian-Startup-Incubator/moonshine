import { json, type RequestHandler } from '@sveltejs/kit';
import { getQueueSummary, listJobs } from '$lib/server/queue';

/** GET /api/jobs[?mine=1] -> {jobs, queue} for polling the /jobs page. */
export const GET: RequestHandler = async ({ url, locals }) => {
	const mine = url.searchParams.get('mine') === '1' && locals.userName;
	const [jobs, queue] = await Promise.all([
		listJobs(mine ? { ownerName: locals.userName! } : {}),
		getQueueSummary()
	]);
	return json({ jobs, queue });
};

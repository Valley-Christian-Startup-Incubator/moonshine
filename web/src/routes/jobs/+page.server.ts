import type { PageServerLoad } from './$types';
import { listJobs } from '$lib/server/jobs';
import { TEAMS } from '$lib/config';
import { JOB_TYPES } from '$lib/types';

export const load: PageServerLoad = async () => {
	const jobs = await listJobs();
	return { jobs, teams: TEAMS, jobTypes: JOB_TYPES };
};

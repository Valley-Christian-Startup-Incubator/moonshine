import { fail, redirect } from '@sveltejs/kit';
import { nanoid } from 'nanoid';
import path from 'node:path';
import { writeFile, mkdir } from 'node:fs/promises';
import type { Actions, PageServerLoad } from './$types';
import { TEAMS, MAX_UPLOAD_BYTES } from '$lib/config';
import { JOB_TYPES, JOB_TYPE_FIELDS, type JobType, type Team, type JobParams } from '$lib/types';
import { JOBS_DIR } from '$lib/server/env';
import { submitJob } from '$lib/server/jobs';

export const load: PageServerLoad = async () => {
	return { teams: TEAMS, jobTypes: JOB_TYPES, fields: JOB_TYPE_FIELDS };
};

export const actions: Actions = {
	default: async ({ request }) => {
		const form = await request.formData();

		const team = form.get('team') as Team | null;
		const type = form.get('jobType') as JobType | null;
		const file = form.get('file') as File | null;

		if (!team || !TEAMS.includes(team)) {
			return fail(400, { error: 'Please select a valid team.' });
		}
		if (!type || !(type in JOB_TYPE_FIELDS)) {
			return fail(400, { error: 'Please select a valid job type.' });
		}
		if (!file || file.size === 0) {
			return fail(400, { error: 'Please attach an input file.' });
		}
		if (file.size > MAX_UPLOAD_BYTES) {
			return fail(400, { error: 'File exceeds the 500MB upload limit.' });
		}

		const jobId = nanoid(10);
		const jobDir = path.join(JOBS_DIR, jobId);
		await mkdir(jobDir, { recursive: true });

		const safeName = file.name.replace(/[^a-zA-Z0-9._-]/g, '_');
		const inputFile = path.join(jobDir, safeName);
		const buf = Buffer.from(await file.arrayBuffer());
		await writeFile(inputFile, buf);

		const params: JobParams = {};
		for (const field of JOB_TYPE_FIELDS[type]) {
			const raw = form.get(field.name);
			if (raw === null || raw === '') {
				params[field.name] = field.default;
			} else {
				params[field.name] = field.type === 'number' ? Number(raw) : String(raw);
			}
		}

		try {
			await submitJob({
				id: jobId,
				team,
				type,
				params,
				inputFile,
				submittedAt: new Date().toISOString()
			});
		} catch (err) {
			return fail(502, {
				error: `Failed to enqueue job with Dagu: ${err instanceof Error ? err.message : String(err)}`
			});
		}

		redirect(303, `/jobs/${jobId}?submitted=1`);
	}
};

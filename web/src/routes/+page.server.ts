import { fail, redirect } from '@sveltejs/kit';
import { nanoid } from 'nanoid';
import path from 'node:path';
import { writeFile, mkdir } from 'node:fs/promises';
import type { Actions, PageServerLoad } from './$types';
import { TEAMS, MAX_UPLOAD_BYTES } from '$lib/config';
import { JOB_TYPES, JOB_TYPE_FIELDS, type JobType, type Team, type JobParams } from '$lib/types';
import { JOBS_DIR } from '$lib/server/env';
import { submitJob } from '$lib/server/jobs';
import { getModelPresetConfig, getModelPresets, type StudentModelId } from '$lib/server/model-presets';

export const load: PageServerLoad = async ({ url }) => {
	const requestedType = url.searchParams.get('jobType');
	const initialType = JOB_TYPES.some((jobType) => jobType.value === requestedType)
		? (requestedType as JobType)
		: 'prompt-gen';
	const modelPresets = await getModelPresets();
	const fields = Object.fromEntries(
		Object.entries(JOB_TYPE_FIELDS).map(([jobType, jobFields]) => [
			jobType,
			jobFields.filter((field) => field.name !== 'MODEL_PATH' && field.name !== 'TEACHER_MODEL_PATH')
		])
	) as typeof JOB_TYPE_FIELDS;
	return { teams: TEAMS, jobTypes: JOB_TYPES, fields, initialType, modelPresets };
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
		const jobType = JOB_TYPES.find((candidate) => candidate.value === type)!;
		if (jobType.requiresInput && (!file || file.size === 0)) {
			return fail(400, { error: `Please attach the ${jobType.inputLabel.toLowerCase()}.` });
		}
		if (jobType.requiresInput && file && !file.name.toLowerCase().endsWith('.jsonl')) {
			return fail(400, { error: 'Please upload a file whose name ends in .jsonl.' });
		}
		if (file && file.size > MAX_UPLOAD_BYTES) {
			return fail(400, { error: 'File exceeds the 500MB upload limit.' });
		}

		const needsStudentModel = type === 'finetune' || type === 'distill' || type === 'quantize';
		const needsTeacherModel = type === 'teacher-gen' || type === 'distill';
		const modelConfig = needsStudentModel || needsTeacherModel ? await getModelPresetConfig() : null;
		const selectedStudentModel = form.get('studentModel');

		if (
			needsStudentModel &&
			(typeof selectedStudentModel !== 'string' ||
				(selectedStudentModel !== 'qwen-4b' && selectedStudentModel !== 'qwen-8b'))
		) {
			return fail(400, {
				error: 'Configure local Qwen model paths before submitting this job.'
			});
		}
		if (
			needsStudentModel &&
			modelConfig?.paths.students[selectedStudentModel as StudentModelId] === undefined
		) {
			return fail(400, {
				error: 'Configure local Qwen model paths before submitting this job.'
			});
		}
		if (needsTeacherModel && modelConfig?.paths.teacher === undefined) {
			return fail(400, {
				error: 'Configure local Qwen model paths before submitting this job.'
			});
		}

		const jobId = nanoid(10);
		let inputFile = '';
		if (jobType.requiresInput && file) {
			const jobDir = path.join(JOBS_DIR, jobId);
			await mkdir(jobDir, { recursive: true });

			const safeName = file.name.replace(/[^a-zA-Z0-9._-]/g, '_');
			inputFile = path.join(jobDir, safeName);
			const buf = Buffer.from(await file.arrayBuffer());
			await writeFile(inputFile, buf);
		}

		const params: JobParams = {};
		for (const field of JOB_TYPE_FIELDS[type]) {
			const raw = form.get(field.name);
			if (raw === null || raw === '') {
				params[field.name] = field.default;
			} else {
				params[field.name] = field.type === 'number' ? Number(raw) : String(raw);
			}
		}

		if (needsStudentModel) {
			params.MODEL_PATH = modelConfig!.paths.students[selectedStudentModel as StudentModelId]!;
		}
		if (type === 'teacher-gen') {
			params.MODEL_PATH = modelConfig!.paths.teacher!;
		}
		if (type === 'distill') {
			params.TEACHER_MODEL_PATH = modelConfig!.paths.teacher!;
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

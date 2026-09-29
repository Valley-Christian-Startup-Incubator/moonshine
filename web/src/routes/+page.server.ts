import { fail, redirect } from '@sveltejs/kit';
import type { Actions, PageServerLoad } from './$types';
import { getTeacherModel } from '$lib/server/ollama';
import { isStudentId, listStudents } from '$lib/server/students';
import {
	DEFAULT_QUESTION_COUNT,
	MAX_QUESTION_COUNT,
	MAX_UPLOAD_BYTES,
	submitGenerateJob,
	submitTrainJob,
	TRAIN_ADVANCED_KEYS,
	validateSourceDocs,
	docRole,
	type TrainAdvanced
} from '$lib/server/queue';

export const load: PageServerLoad = async () => {
	const [teacher, students] = await Promise.all([getTeacherModel(), listStudents()]);
	return {
		teacher,
		students: students.map(({ id, label, params, available }) => ({ id, label, params, available })),
		defaultCount: DEFAULT_QUESTION_COUNT,
		maxCount: MAX_QUESTION_COUNT
	};
};

function uploaded(form: FormData, key: string): File | null {
	const f = form.get(key);
	return f instanceof File && f.size > 0 ? f : null;
}

export const actions: Actions = {
	generate: async ({ request, locals }) => {
		if (!locals.userName) return fail(401, { stage: 'questions', error: 'Pick your name first.' });
		const form = await request.formData();
		const count = Number(form.get('count') || DEFAULT_QUESTION_COUNT);
		const source = form.get('source');
		const promptText = String(form.get('promptText') ?? '');
		const file = uploaded(form, 'file');
		// one role per chosen document, in the same order as the files (see the Generate form)
		const allDocs = form.getAll('docs');
		const roles = form.getAll('docRole').map(String);
		const docFiles = allDocs.filter((f): f is File => f instanceof File && f.size > 0);
		const docRoles = allDocs
			.map((f, i) => ({ f, role: docRole(roles[i]) }))
			.filter((x) => x.f instanceof File && x.f.size > 0)
			.map((x) => x.role);
		try {
			validateSourceDocs(docFiles.map((f) => ({ name: f.name, size: f.size })));
		} catch (e) {
			return fail(400, { stage: 'questions', error: e instanceof Error ? e.message : String(e) });
		}
		let promptFile: { name: string; data: Uint8Array };
		if (source === 'session') {
			if (promptText.trim().length < 20) return fail(400, { stage: 'questions', error: 'There is no PROMPT from this session yet.' });
			promptFile = { name: 'prompt-from-interview.md', data: new TextEncoder().encode(promptText) };
		} else {
			if (!file) return fail(400, { stage: 'questions', error: 'Choose your PROMPT file (.md or .txt).' });
			promptFile = { name: file.name, data: new Uint8Array(await file.arrayBuffer()) };
		}
		let id: string;
		try {
			const docs = await Promise.all(
				docFiles.map(async (f, i) => ({ name: f.name, data: new Uint8Array(await f.arrayBuffer()), role: docRoles[i] }))
			);
			id = await submitGenerateJob({ ownerName: locals.userName, promptFile, count, docs });
		} catch (e) {
			return fail(400, { stage: 'questions', error: e instanceof Error ? e.message : String(e) });
		}
		redirect(303, `/jobs/${id}?submitted=1`);
	},

	train: async ({ request, locals }) => {
		if (!locals.userName) return fail(401, { stage: 'train', error: 'Pick your name first.' });
		const form = await request.formData();
		const student = form.get('student');
		const file = uploaded(form, 'file');
		if (!isStudentId(student)) return fail(400, { stage: 'train', error: 'Choose a student model.' });
		if (!file) return fail(400, { stage: 'train', error: 'Choose your questions file (.jsonl).' });
		if (file.size > MAX_UPLOAD_BYTES) return fail(400, { stage: 'train', error: 'The file is larger than 500 MB.' });
		const advanced: TrainAdvanced = {};
		for (const key of TRAIN_ADVANCED_KEYS) {
			const raw = form.get(key);
			if (typeof raw === 'string' && raw.trim() !== '') {
				const n = Number(raw);
				if (!Number.isFinite(n) || n <= 0) return fail(400, { stage: 'train', error: `${key} must be a positive number.` });
				advanced[key] = n;
			}
		}
		let id: string;
		try {
			id = await submitTrainJob({
				ownerName: locals.userName,
				student,
				dataFile: { name: file.name, data: new Uint8Array(await file.arrayBuffer()) },
				advanced
			});
		} catch (e) {
			return fail(400, { stage: 'train', error: e instanceof Error ? e.message : String(e) });
		}
		redirect(303, `/jobs/${id}?submitted=1`);
	}
};

import { fail } from '@sveltejs/kit';
import type { Actions, PageServerLoad } from './$types';
import { listJobs, cancelJob } from '$lib/server/jobs';
import { isDaguHealthy } from '$lib/server/dagu';
import { getDiskUsage } from '$lib/server/health';
import { ADMIN_PASSWORD, ADMIN_SESSION_COOKIE } from '$lib/server/env';
import {
	getStudentSettings,
	getTeacherSettings,
	saveStudentModelPath,
	saveTeacherModelPath,
	type StudentModelId
} from '$lib/server/model-presets';

const STUDENT_MODEL_IDS = ['qwen-4b', 'qwen-8b'] as const;

function isStudentModelId(value: unknown): value is StudentModelId {
	return typeof value === 'string' && (STUDENT_MODEL_IDS as readonly string[]).includes(value);
}

function isAuthed(cookies: { get(name: string): string | undefined }): boolean {
	return cookies.get(ADMIN_SESSION_COOKIE) === ADMIN_PASSWORD;
}

export const load: PageServerLoad = async ({ cookies }) => {
	if (!isAuthed(cookies)) {
		return { authed: false as const };
	}

	const [jobs, daguHealthy, diskUsage, teacherSettings, studentSettings] = await Promise.all([
		listJobs(),
		isDaguHealthy(),
		getDiskUsage(),
		getTeacherSettings(),
		getStudentSettings()
	]);

	return { authed: true as const, jobs, daguHealthy, diskUsage, teacherSettings, studentSettings };
};

export const actions: Actions = {
	login: async ({ request, cookies }) => {
		const form = await request.formData();
		const password = form.get('password');
		if (password !== ADMIN_PASSWORD) {
			return fail(401, { error: 'Incorrect password.' });
		}
		cookies.set(ADMIN_SESSION_COOKIE, ADMIN_PASSWORD, {
			path: '/',
			httpOnly: true,
			sameSite: 'lax',
			maxAge: 60 * 60 * 12
		});
		return { success: true };
	},

	logout: async ({ cookies }) => {
		cookies.delete(ADMIN_SESSION_COOKIE, { path: '/' });
		return { success: true };
	},

	saveTeacher: async ({ request, cookies }) => {
		if (!isAuthed(cookies)) return fail(401, { error: 'Not authorized.' });
		const form = await request.formData();
		const raw = form.get('teacherPath');
		const teacherPath = typeof raw === 'string' ? raw : '';
		try {
			await saveTeacherModelPath(teacherPath);
			return { teacherSaved: true };
		} catch (error) {
			return fail(400, {
				teacherError: error instanceof Error ? error.message : String(error),
				teacherPath
			});
		}
	},

	saveStudent: async ({ request, cookies }) => {
		if (!isAuthed(cookies)) return fail(401, { error: 'Not authorized.' });
		const form = await request.formData();
		const rawId = form.get('studentId');
		const rawPath = form.get('studentPath');
		const studentId = typeof rawId === 'string' ? rawId : '';
		const studentPath = typeof rawPath === 'string' ? rawPath : '';
		if (!isStudentModelId(studentId)) {
			return fail(400, {
				studentError: 'Please select a valid student model.',
				studentId,
				studentPath
			});
		}
		try {
			await saveStudentModelPath(studentId, studentPath);
			return { studentSaved: studentId };
		} catch (error) {
			return fail(400, {
				studentError: error instanceof Error ? error.message : String(error),
				studentId,
				studentPath
			});
		}
	},

	cancel: async ({ request, cookies }) => {
		if (!isAuthed(cookies)) return fail(401, { error: 'Not authorized.' });
		const form = await request.formData();
		const jobId = form.get('jobId') as string;
		const ok = await cancelJob(jobId);
		if (!ok) return fail(500, { error: `Failed to cancel job ${jobId}.` });
		return { success: true };
	}
};

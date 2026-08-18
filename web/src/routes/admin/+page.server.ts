import { fail } from '@sveltejs/kit';
import type { Actions, PageServerLoad } from './$types';
import { listJobs, cancelJob } from '$lib/server/jobs';
import { isDaguHealthy } from '$lib/server/dagu';
import { getDiskUsage } from '$lib/server/health';
import { ADMIN_PASSWORD, ADMIN_SESSION_COOKIE } from '$lib/server/env';

function isAuthed(cookies: { get(name: string): string | undefined }): boolean {
	return cookies.get(ADMIN_SESSION_COOKIE) === ADMIN_PASSWORD;
}

export const load: PageServerLoad = async ({ cookies }) => {
	if (!isAuthed(cookies)) {
		return { authed: false as const };
	}

	const [jobs, daguHealthy, diskUsage] = await Promise.all([
		listJobs(),
		isDaguHealthy(),
		getDiskUsage()
	]);

	return { authed: true as const, jobs, daguHealthy, diskUsage };
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

	cancel: async ({ request, cookies }) => {
		if (!isAuthed(cookies)) return fail(401, { error: 'Not authorized.' });
		const form = await request.formData();
		const jobId = form.get('jobId') as string;
		const ok = await cancelJob(jobId);
		if (!ok) return fail(500, { error: `Failed to cancel job ${jobId}.` });
		return { success: true };
	}
};

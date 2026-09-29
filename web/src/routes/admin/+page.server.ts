import { fail } from '@sveltejs/kit';
import type { Actions, PageServerLoad } from './$types';
import { checkAdminPassword, clearAdminSession, isAdmin, setAdminSession } from '$lib/server/admin';
import { listUsers } from '$lib/server/users';
import { listJobs } from '$lib/server/queue';
import { JOB_TYPE_LABELS } from '$lib/types';

export const load: PageServerLoad = async ({ cookies }) => {
	if (!isAdmin(cookies)) return { authed: false as const, users: [], jobs: [] };
	const jobs = (await listJobs()).map((j) => ({
		id: j.id,
		owner: j.ownerName ?? '(none)',
		type: JOB_TYPE_LABELS[j.type] ?? j.type,
		status: j.status,
		pending: j.pendingAction ?? null,
		submittedAt: j.submittedAt,
		ollamaTag: j.ollamaTag ?? null
	}));
	return { authed: true as const, users: await listUsers(), jobs };
};

export const actions: Actions = {
	login: async ({ request, cookies, url }) => {
		const form = await request.formData();
		if (!checkAdminPassword(form.get('password'))) return fail(401, { error: 'Incorrect password.' });
		setAdminSession(cookies, url.protocol === 'https:');
		return { success: true };
	},
	logout: async ({ cookies }) => {
		clearAdminSession(cookies);
		return { success: true };
	}
};

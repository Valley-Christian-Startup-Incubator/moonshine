import { fail } from '@sveltejs/kit';
import type { Actions, PageServerLoad } from './$types';
import { checkAdminPassword, clearAdminSession, isAdmin, setAdminSession } from '$lib/server/admin';
import { listUsers } from '$lib/server/users';

export const load: PageServerLoad = async ({ cookies }) => {
	if (!isAdmin(cookies)) return { authed: false as const, users: [] };
	return { authed: true as const, users: await listUsers() };
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

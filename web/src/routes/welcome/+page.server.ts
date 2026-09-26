import { fail, redirect } from '@sveltejs/kit';
import type { Actions, PageServerLoad } from './$types';
import { safeNextPath } from '$lib/server/auth';
import { addUser, listUsers, selectUser } from '$lib/server/users';

export const load: PageServerLoad = async ({ locals, url }) => ({
	users: await listUsers(),
	current: locals.userName,
	next: safeNextPath(url.searchParams.get('next'))
});

export const actions: Actions = {
	select: async ({ request, cookies, url }) => {
		const form = await request.formData();
		try {
			await selectUser(cookies, form.get('name'), url.protocol === 'https:');
		} catch (e) {
			return fail(400, { error: e instanceof Error ? e.message : String(e) });
		}
		redirect(303, safeNextPath(form.get('next')));
	},
	add: async ({ request, cookies, url }) => {
		const form = await request.formData();
		try {
			const user = await addUser(form.get('name'));
			await selectUser(cookies, user.name, url.protocol === 'https:');
		} catch (e) {
			return fail(400, { error: e instanceof Error ? e.message : String(e), name: String(form.get('name') ?? '') });
		}
		redirect(303, safeNextPath(form.get('next')));
	}
};

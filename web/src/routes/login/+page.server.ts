import { fail, redirect } from '@sveltejs/kit';
import type { Actions, PageServerLoad } from './$types';
import {
	isWebAuthEnabled,
	isWebPasswordValid,
	isWebSessionValid,
	webSessionToken,
	WEB_SESSION_COOKIE
} from '$lib/server/auth';

function safeDestination(value: string | null): string {
	return value?.startsWith('/') && !value.startsWith('//') ? value : '/';
}

export const load: PageServerLoad = async ({ cookies, url }) => {
	if (!isWebAuthEnabled() || isWebSessionValid(cookies.get(WEB_SESSION_COOKIE))) {
		redirect(303, safeDestination(url.searchParams.get('next')));
	}
	return {};
};

export const actions: Actions = {
	default: async ({ request, cookies, url }) => {
		const form = await request.formData();
		const password = form.get('password');
		if (typeof password !== 'string' || !isWebPasswordValid(password)) {
			return fail(401, { error: 'Incorrect password.' });
		}

		cookies.set(WEB_SESSION_COOKIE, webSessionToken(), {
			path: '/',
			httpOnly: true,
			sameSite: 'lax',
			secure: url.protocol === 'https:',
			maxAge: 60 * 60 * 12
		});
		redirect(303, safeDestination(url.searchParams.get('next')));
	}
};

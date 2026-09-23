import { fail, redirect } from '@sveltejs/kit';
import type { Actions, PageServerLoad } from './$types';
import {
	authenticateUser,
	clearLoginFailures,
	createSession,
	getUserFromSession,
	isLoginThrottled,
	noteLoginFailure,
	safeNextPath,
	SESSION_MAX_AGE_SECONDS,
	waitForLoginThrottle,
	WEB_SESSION_COOKIE
} from '$lib/server/auth';

function getThrottleKey(getClientAddress: () => string): string {
	try {
		return getClientAddress() || 'unknown';
	} catch {
		return 'unknown';
	}
}

function formString(form: FormData, name: string): string {
	const value = form.get(name);
	return typeof value === 'string' ? value : '';
}

export const load: PageServerLoad = async ({ cookies, url }) => {
	if (await getUserFromSession(cookies.get(WEB_SESSION_COOKIE))) {
		redirect(303, safeNextPath(url.searchParams.get('next')));
	}
};

export const actions: Actions = {
	default: async ({ request, cookies, url, getClientAddress }) => {
		const form = await request.formData();
		const password = formString(form, 'password');
		const throttleKey = getThrottleKey(getClientAddress);

		if (isLoginThrottled(throttleKey)) {
			return fail(429, { error: 'Invalid password.' });
		}
		await waitForLoginThrottle(throttleKey);
		if (!authenticateUser(password)) {
			noteLoginFailure(throttleKey);
			return fail(401, { error: 'Invalid password.' });
		}

		clearLoginFailures(throttleKey);
		const token = await createSession();
		cookies.set(WEB_SESSION_COOKIE, token, {
			path: '/',
			httpOnly: true,
			sameSite: 'lax',
			secure: url.protocol === 'https:',
			maxAge: SESSION_MAX_AGE_SECONDS
		});
		redirect(303, safeNextPath(formString(form, 'next') || url.searchParams.get('next')));
	}
};

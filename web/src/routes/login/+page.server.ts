import { fail, redirect } from '@sveltejs/kit';
import type { Actions, PageServerLoad } from './$types';
import {
	AccountExistsError,
	InvalidInviteCodeError,
	InvalidPasswordError,
	InvalidUsernameError,
	authenticateUser,
	clearLoginFailures,
	createSession,
	getUserFromSession,
	isInviteRequired,
	isLoginThrottled,
	noteLoginFailure,
	registerAccount,
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

function destination(form: FormData, searchNext: string | null): string {
	return safeNextPath(formString(form, 'next') || searchNext);
}

function setSessionCookie(
	cookies: Parameters<NonNullable<Actions['login']>>[0]['cookies'],
	token: string,
	secure: boolean
): void {
	cookies.set(WEB_SESSION_COOKIE, token, {
		path: '/',
		httpOnly: true,
		sameSite: 'lax',
		secure,
		maxAge: SESSION_MAX_AGE_SECONDS
	});
}

export const load: PageServerLoad = async ({ cookies, url }) => {
	if (await getUserFromSession(cookies.get(WEB_SESSION_COOKIE))) {
		redirect(303, safeNextPath(url.searchParams.get('next')));
	}
	return { inviteRequired: isInviteRequired() };
};

export const actions: Actions = {
	login: async ({ request, cookies, url, getClientAddress }) => {
		const form = await request.formData();
		const username = formString(form, 'username');
		const password = formString(form, 'password');
		const throttleKey = getThrottleKey(getClientAddress);

		if (isLoginThrottled(throttleKey)) {
			return fail(429, { error: 'Invalid username or password.' });
		}
		await waitForLoginThrottle(throttleKey);
		const user = await authenticateUser(username, password);
		if (!user) {
			noteLoginFailure(throttleKey);
			return fail(401, { error: 'Invalid username or password.' });
		}

		clearLoginFailures(throttleKey);
		const token = await createSession(user);
		setSessionCookie(cookies, token, url.protocol === 'https:');
		redirect(303, destination(form, url.searchParams.get('next')));
	},

	register: async ({ request, cookies, url, getClientAddress }) => {
		const form = await request.formData();
		const username = formString(form, 'username');
		const password = formString(form, 'password');
		const inviteCode = formString(form, 'inviteCode');
		const throttleKey = getThrottleKey(getClientAddress);

		if (isLoginThrottled(throttleKey)) {
			return fail(429, { error: 'Unable to create account right now.' });
		}
		await waitForLoginThrottle(throttleKey);

		let user;
		try {
			user = await registerAccount(username, password, inviteCode);
		} catch (error) {
			noteLoginFailure(throttleKey);
			if (error instanceof InvalidUsernameError || error instanceof InvalidPasswordError) {
				return fail(400, { error: error.message });
			}
			if (error instanceof InvalidInviteCodeError) {
				return fail(400, { error: 'The invite code is invalid.' });
			}
			if (error instanceof AccountExistsError) {
				return fail(409, { error: 'That username is already registered.' });
			}
			return fail(500, { error: 'Unable to create account right now.' });
		}

		clearLoginFailures(throttleKey);
		const token = await createSession(user);
		setSessionCookie(cookies, token, url.protocol === 'https:');
		redirect(303, destination(form, url.searchParams.get('next')));
	}
};

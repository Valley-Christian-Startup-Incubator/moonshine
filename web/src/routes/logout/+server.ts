import { redirect } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { revokeSession, WEB_SESSION_COOKIE } from '$lib/server/auth';
import { ADMIN_SESSION_COOKIE } from '$lib/server/env';

export const POST: RequestHandler = async ({ cookies }) => {
	await revokeSession(cookies.get(WEB_SESSION_COOKIE));
	cookies.delete(WEB_SESSION_COOKIE, { path: '/' });
	cookies.delete(ADMIN_SESSION_COOKIE, { path: '/' });
	redirect(303, '/login');
};

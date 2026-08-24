import { redirect, type Handle } from '@sveltejs/kit';
import {
	isWebAuthEnabled,
	isWebSessionValid,
	WEB_SESSION_COOKIE
} from '$lib/server/auth';

const PUBLIC_PATHS = new Set(['/login', '/favicon.svg']);

export const handle: Handle = async ({ event, resolve }) => {
	const { pathname, search } = event.url;
	const isPublic = PUBLIC_PATHS.has(pathname) || pathname.startsWith('/_app/');

	if (
		isWebAuthEnabled() &&
		!isPublic &&
		!isWebSessionValid(event.cookies.get(WEB_SESSION_COOKIE))
	) {
		const destination = `${pathname}${search}`;
		redirect(303, `/login?next=${encodeURIComponent(destination)}`);
	}

	return resolve(event);
};

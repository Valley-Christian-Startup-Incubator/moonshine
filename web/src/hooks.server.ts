import { redirect, type Handle } from '@sveltejs/kit';
import { getUserFromSession, WEB_SESSION_COOKIE } from '$lib/server/auth';

const PUBLIC_PATHS = new Set(['/login', '/favicon.svg']);

export const handle: Handle = async ({ event, resolve }) => {
	const { pathname, search } = event.url;
	const user = await getUserFromSession(event.cookies.get(WEB_SESSION_COOKIE));
	event.locals.user = user;

	const isPublic =
		PUBLIC_PATHS.has(pathname) || pathname === '/_app' || pathname.startsWith('/_app/');
	if (!isPublic && !user) {
		const destination = `${pathname}${search}`;
		redirect(303, `/login?next=${encodeURIComponent(destination)}`);
	}

	return resolve(event);
};

import { redirect, type Handle } from '@sveltejs/kit';
import { getUserFromSession, WEB_SESSION_COOKIE } from '$lib/server/auth';
import { getCurrentUserName } from '$lib/server/users';

const PUBLIC_PATHS = new Set(['/login', '/favicon.svg']);

/** Paths a signed-in person may use before picking a name. */
function allowedWithoutName(pathname: string): boolean {
	return (
		pathname === '/welcome' ||
		pathname === '/logout' ||
		pathname.startsWith('/api/') ||
		pathname === '/admin' ||
		pathname.startsWith('/admin/')
	);
}

export const handle: Handle = async ({ event, resolve }) => {
	const { pathname, search } = event.url;
	const user = await getUserFromSession(event.cookies.get(WEB_SESSION_COOKIE));
	event.locals.user = user;
	event.locals.userName = user ? await getCurrentUserName(event.cookies) : null;

	const isPublic =
		PUBLIC_PATHS.has(pathname) || pathname === '/_app' || pathname.startsWith('/_app/');
	if (!isPublic && !user) {
		redirect(303, `/login?next=${encodeURIComponent(`${pathname}${search}`)}`);
	}
	if (user && !event.locals.userName && !isPublic && !allowedWithoutName(pathname)) {
		redirect(303, `/welcome?next=${encodeURIComponent(`${pathname}${search}`)}`);
	}

	return resolve(event);
};

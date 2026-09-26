import { json, type RequestHandler } from '@sveltejs/kit';
import { clearSelectedUser, selectUser } from '$lib/server/users';

/** POST {name} -> select an existing name. POST {name: null} -> clear (switch user). */
export const POST: RequestHandler = async ({ request, cookies, url }) => {
	const body = (await request.json().catch(() => ({}))) as { name?: unknown };
	if (body.name === null) {
		clearSelectedUser(cookies);
		return json({ ok: true });
	}
	try {
		return json({ ok: true, user: await selectUser(cookies, body.name, url.protocol === 'https:') });
	} catch (e) {
		return json({ ok: false, error: e instanceof Error ? e.message : String(e) }, { status: 400 });
	}
};

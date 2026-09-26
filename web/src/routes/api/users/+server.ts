import { json, type RequestHandler } from '@sveltejs/kit';
import { addUser, listUsers, selectUser } from '$lib/server/users';

/** GET -> {users, current}. POST {name} -> add (if new) and select it. */
export const GET: RequestHandler = async ({ locals }) =>
	json({ users: await listUsers(), current: locals.userName ?? null });

export const POST: RequestHandler = async ({ request, cookies, url }) => {
	const body = (await request.json().catch(() => ({}))) as { name?: unknown };
	try {
		const user = await addUser(body.name);
		await selectUser(cookies, user.name, url.protocol === 'https:');
		return json({ ok: true, user });
	} catch (e) {
		return json({ ok: false, error: e instanceof Error ? e.message : String(e) }, { status: 400 });
	}
};

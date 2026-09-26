import { json, type RequestHandler } from '@sveltejs/kit';
import { isAdmin } from '$lib/server/admin';
import { deleteUser, previewDeleteUser } from '$lib/server/users';

/** GET ?name=X -> what would be deleted (for the warning popup). POST {name} -> delete. Admin cookie required. */
export const GET: RequestHandler = async ({ url, cookies }) => {
	if (!isAdmin(cookies)) return json({ error: 'Not authorized.' }, { status: 401 });
	return json(await previewDeleteUser(url.searchParams.get('name') ?? ''));
};

export const POST: RequestHandler = async ({ request, cookies }) => {
	if (!isAdmin(cookies)) return json({ error: 'Not authorized.' }, { status: 401 });
	const body = (await request.json().catch(() => ({}))) as { name?: unknown };
	if (typeof body.name !== 'string') return json({ error: 'Name required.' }, { status: 400 });
	try {
		return json({ ok: true, ...(await deleteUser(body.name)) });
	} catch (e) {
		return json({ ok: false, error: e instanceof Error ? e.message : String(e) }, { status: 400 });
	}
};

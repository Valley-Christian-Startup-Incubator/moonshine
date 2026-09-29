import { json, type RequestHandler } from '@sveltejs/kit';
import { isAdmin } from '$lib/server/admin';
import { adminDeleteJob } from '$lib/server/job-delete';

/** POST {id} -> delete one job (any owner) through the same purge path as the owner delete. Admin cookie required. */
export const POST: RequestHandler = async ({ request, cookies }) => {
	if (!isAdmin(cookies)) return json({ error: 'Not authorized.' }, { status: 401 });
	const body = (await request.json().catch(() => ({}))) as { id?: unknown };
	if (typeof body.id !== 'string' || !body.id) return json({ error: 'Job id required.' }, { status: 400 });
	try {
		const r = await adminDeleteJob(body.id);
		return r.ok ? json(r) : json({ ok: false, error: r.error }, { status: r.status });
	} catch (e) {
		return json({ ok: false, error: e instanceof Error ? e.message : String(e) }, { status: 400 });
	}
};

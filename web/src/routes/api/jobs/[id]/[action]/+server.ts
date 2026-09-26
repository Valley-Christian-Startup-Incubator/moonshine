import { json, type RequestHandler } from '@sveltejs/kit';
import { cancelJob, pauseJob, resumeJob, retryJob } from '$lib/server/queue';

/** POST /api/jobs/:id/pause | resume | cancel | retry (retry body: {overrides?}) */
export const POST: RequestHandler = async ({ params, request, locals }) => {
	if (!locals.user || !locals.userName) return json({ error: 'Pick your name first.' }, { status: 401 });
	const id = params.id ?? '';
	switch (params.action) {
		case 'pause':
			return result(await pauseJob(id));
		case 'resume':
			return result(await resumeJob(id));
		case 'cancel':
			return result(await cancelJob(id));
		case 'retry': {
			const body = (await request.json().catch(() => ({}))) as { overrides?: Record<string, string | number> };
			try {
				return json({ ok: true, ...(await retryJob(id, locals.userName, body.overrides ?? {})) });
			} catch (e) {
				return json({ ok: false, error: e instanceof Error ? e.message : String(e) }, { status: 400 });
			}
		}
		default:
			return json({ error: 'Unknown action' }, { status: 404 });
	}
};

function result(r: { ok: true } | { ok: false; error: string }) {
	return json(r, { status: r.ok ? 200 : 409 });
}

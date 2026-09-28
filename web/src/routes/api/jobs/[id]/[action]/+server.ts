import { json, type RequestHandler } from '@sveltejs/kit';
import { cancelJob, getJob, isJobOwner, pauseJob, resumeJob, retryJob } from '$lib/server/queue';

/** POST /api/jobs/:id/pause | resume | cancel | retry (retry body: {overrides?}) */
export const POST: RequestHandler = async ({ params, request, locals }) => {
	if (!locals.user || !locals.userName) return json({ error: 'Pick your name first.' }, { status: 401 });
	const id = params.id ?? '';
	const job = await getJob(id);
	if (!job) return json({ ok: false, error: 'Job not found.' }, { status: 404 });
	// Only the job's owner may pause, resume, cancel or retry it in place. Anyone may retry as a NEW job
	// (retry with overrides), which is created under their own name. /admin is separate and unaffected.
	const inPlace = params.action !== 'retry' || !(await peekOverrides(request));
	if (['pause', 'resume', 'cancel', 'retry'].includes(params.action ?? '') && inPlace && !isJobOwner(job, locals.userName))
		return json({ ok: false, error: `Only ${job.ownerName} can ${params.action} this job.` }, { status: 403 });
	switch (params.action) {
		case 'pause':
			return result(await pauseJob(id));
		case 'resume':
			return result(await resumeJob(id));
		case 'cancel':
			return result(await cancelJob(id));
		case 'retry': {
			const body = (await request.clone().json().catch(() => ({}))) as { overrides?: Record<string, string | number> };
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

async function peekOverrides(request: Request): Promise<boolean> {
	const body = (await request.clone().json().catch(() => ({}))) as { overrides?: Record<string, unknown> };
	return Boolean(body.overrides && Object.keys(body.overrides).length);
}

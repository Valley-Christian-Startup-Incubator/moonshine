import { error, json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { getJob, listJobArtifacts } from '$lib/server/queue';
import { UnsafeResultPathError, validateResultPath } from '$lib/server/downloads';
import { readJsonlPreview } from '$lib/server/jsonl-preview';

export const GET: RequestHandler = async ({ params, url }) => {
	const job = await getJob(params.id);
	if (!job) error(404, 'Job not found');
	const artifact = (await listJobArtifacts(job.id)).find((a) => a.key === url.searchParams.get('file'));
	if (!artifact || artifact.kind !== 'file' || !artifact.relPath.endsWith('.jsonl')) {
		error(404, 'That JSONL file is not available yet');
	}
	const cursor = url.searchParams.get('offset') ?? '0';
	if (!/^\d+$/.test(cursor) || !Number.isSafeInteger(Number(cursor))) error(400, 'Invalid preview position');
	try {
		const result = await validateResultPath(job.id, artifact.relPath);
		if (!result.stats.isFile()) error(404, 'Result is not a file');
		return json(await readJsonlPreview(result.path, Number(cursor)), {
			headers: { 'Cache-Control': 'private, no-store' }
		});
	} catch (cause) {
		if (cause instanceof RangeError) error(400, cause.message);
		if (cause instanceof UnsafeResultPathError) error(400, 'Invalid result path');
		error(404, 'Result could not be read from disk');
	}
};

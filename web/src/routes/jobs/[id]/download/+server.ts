import { error } from '@sveltejs/kit';
import { stat, readdir } from 'node:fs/promises';
import { createReadStream } from 'node:fs';
import path from 'node:path';
import type { RequestHandler } from './$types';
import { getJob } from '$lib/server/jobs';
import { RESULTS_DIR } from '$lib/server/env';

/**
 * Streams the job's result file. If the output is a directory (e.g. a
 * quantized model), streams the results dir listing instead — operators
 * should tar/zip large model directories manually or fetch them over the
 * local network via `scp`/`rsync`.
 */
export const GET: RequestHandler = async ({ params }) => {
	const job = await getJob(params.id);
	if (!job) error(404, 'Job not found');
	if (job.status !== 'complete') error(400, 'Job has not completed yet');

	const outputPath = job.outputPath ?? path.join(RESULTS_DIR, job.id, 'output.jsonl');
	const stats = await stat(outputPath).catch(() => null);
	if (!stats) error(404, 'Result file not found on disk');

	if (stats.isDirectory()) {
		const entries = await readdir(outputPath);
		return new Response(JSON.stringify({ directory: outputPath, files: entries }, null, 2), {
			headers: { 'Content-Type': 'application/json' }
		});
	}

	const nodeStream = createReadStream(outputPath);
	const webStream = new ReadableStream({
		start(controller) {
			nodeStream.on('data', (chunk) => controller.enqueue(chunk));
			nodeStream.on('end', () => controller.close());
			nodeStream.on('error', (err) => controller.error(err));
		},
		cancel() {
			nodeStream.destroy();
		}
	});

	return new Response(webStream, {
		headers: {
			'Content-Type': 'application/octet-stream',
			'Content-Disposition': `attachment; filename="${job.id}-${path.basename(outputPath)}"`,
			'Content-Length': String(stats.size)
		}
	});
};

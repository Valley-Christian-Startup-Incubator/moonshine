import { error } from '@sveltejs/kit';
import { spawn } from 'node:child_process';
import { createReadStream } from 'node:fs';
import { PassThrough, Readable } from 'node:stream';
import path from 'node:path';
import type { RequestHandler } from './$types';
import { getJob, listJobArtifacts } from '$lib/server/queue';
import { ResultPathMissingError, UnsafeResultPathError, validateResultPath } from '$lib/server/downloads';

function safeFilename(value: string): string {
	const cleaned = value.replace(/[^A-Za-z0-9._-]/g, '_').replace(/^\.+$/, '_');
	return cleaned.slice(0, 180) || 'result';
}

function tarStream(jobDir: string, relativePath: string): ReadableStream<Uint8Array> {
	const child = spawn('tar', ['-czf', '-', '--', relativePath], { cwd: jobDir, shell: false, stdio: ['ignore', 'pipe', 'pipe'] });
	const output = new PassThrough();
	let exited = false;
	const terminate = () => {
		if (exited) return;
		child.kill('SIGTERM');
		setTimeout(() => !exited && child.kill('SIGKILL'), 1000).unref();
	};
	child.stderr.resume();
	child.stdout.pipe(output, { end: false });
	child.stdout.on('error', (c) => output.destroy(c));
	child.on('error', (c) => output.destroy(c));
	child.on('close', (code, signal) => {
		exited = true;
		if (code === 0) output.end();
		else output.destroy(new Error(`Archive failed${signal ? ` (${signal})` : ` (exit ${code ?? '?'})`}.`));
	});
	output.on('close', terminate);
	output.on('error', terminate);
	return Readable.toWeb(output) as ReadableStream<Uint8Array>;
}

/** GET /jobs/:id/download?file=<artifact key> (questions, report-md, report-json, adapters, fused, ollama-gguf, log). */
export const GET: RequestHandler = async ({ params, url }) => {
	const job = await getJob(params.id);
	if (!job) error(404, 'Job not found');
	const key = url.searchParams.get('file') ?? (job.type === 'generate' ? 'questions' : 'report-md');
	const artifact = (await listJobArtifacts(job.id)).find((a) => a.key === key);
	if (!artifact) error(404, 'That result is not available yet');

	let result;
	try {
		result = await validateResultPath(job.id, artifact.relPath);
	} catch (cause) {
		if (cause instanceof UnsafeResultPathError) error(400, 'Invalid result path');
		if (cause instanceof ResultPathMissingError) error(404, 'Result not found on disk');
		error(404, 'Result not found on disk');
	}
	const owner = safeFilename(job.ownerName.toLowerCase());
	if (result.stats.isDirectory()) {
		const rel = path.relative(result.jobDir, result.path) || '.';
		return new Response(tarStream(result.jobDir, rel), {
			headers: {
				'Content-Type': 'application/gzip',
				'Content-Disposition': `attachment; filename="${owner}-${safeFilename(job.id)}-${safeFilename(artifact.key)}.tar.gz"`
			}
		});
	}
	return new Response(Readable.toWeb(createReadStream(result.path)) as ReadableStream<Uint8Array>, {
		headers: {
			'Content-Type': result.path.endsWith('.md')
				? 'text/markdown; charset=utf-8'
				: result.path.endsWith('.zip')
					? 'application/zip'
					: 'application/octet-stream',
			'Content-Disposition': `attachment; filename="${owner}-${safeFilename(job.id)}-${safeFilename(path.basename(result.path))}"`,
			'Content-Length': String(result.stats.size)
		}
	});
};

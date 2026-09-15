import { error } from '@sveltejs/kit';
import { spawn } from 'node:child_process';
import { createReadStream } from 'node:fs';
import { PassThrough, Readable } from 'node:stream';
import path from 'node:path';
import type { RequestHandler } from './$types';
import { getJob } from '$lib/server/jobs';
import {
	ResultPathMissingError,
	UnsafeResultPathError,
	validateResultPath
} from '$lib/server/downloads';

function safeFilename(value: string): string {
	const cleaned = value.replace(/[^A-Za-z0-9._-]/g, '_').replace(/^\.+$/, '_');
	return cleaned.slice(0, 180) || 'result';
}

function tarStream(jobDir: string, relativePath: string): ReadableStream<Uint8Array> {
	const child = spawn('tar', ['-czf', '-', '--', relativePath], {
		cwd: jobDir,
		shell: false,
		stdio: ['ignore', 'pipe', 'pipe']
	});
	const output = new PassThrough();
	let childExited = false;
	let terminating = false;

	const terminate = () => {
		if (childExited || terminating) return;
		terminating = true;
		child.kill('SIGTERM');
		setTimeout(() => {
			if (!childExited) child.kill('SIGKILL');
		}, 1000).unref();
	};

	child.stderr.resume();
	child.stdout.pipe(output, { end: false });
	child.stdout.on('error', (cause) => output.destroy(cause));
	child.on('error', (cause) => output.destroy(cause));
	child.on('close', (code, signal) => {
		childExited = true;
		if (code === 0) {
			output.end();
			return;
		}
		output.destroy(
			new Error(`Result archive process failed${signal ? ` (${signal})` : ` (exit ${code ?? 'unknown'})`}.`)
		);
	});
	output.on('close', terminate);
	output.on('error', terminate);

	return Readable.toWeb(output) as ReadableStream<Uint8Array>;
}

export const GET: RequestHandler = async ({ params }) => {
	const job = await getJob(params.id);
	if (!job) error(404, 'Job not found');
	if (job.status !== 'complete') error(400, 'Job has not completed yet');

	let result;
	try {
		result = await validateResultPath(job.id, job.outputPath);
	} catch (cause) {
		if (cause instanceof UnsafeResultPathError) error(400, 'Invalid result path');
		if (cause instanceof ResultPathMissingError) error(404, 'Result file not found on disk');
		error(404, 'Result file not found on disk');
	}

	if (result.stats.isDirectory()) {
		const relativePath = path.relative(result.jobDir, result.path) || '.';
		return new Response(tarStream(result.jobDir, relativePath), {
			headers: {
				'Content-Type': 'application/gzip',
				'Content-Disposition': `attachment; filename="${safeFilename(job.id)}.tar.gz"`
			}
		});
	}

	const nodeStream = createReadStream(result.path);
	return new Response(Readable.toWeb(nodeStream) as ReadableStream<Uint8Array>, {
		headers: {
			'Content-Type': 'application/octet-stream',
			'Content-Disposition': `attachment; filename="${safeFilename(job.id)}-${safeFilename(path.basename(result.path))}"`,
			'Content-Length': String(result.stats.size)
		}
	});
};

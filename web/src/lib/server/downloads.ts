import { readdir, realpath, stat } from 'node:fs/promises';
import path from 'node:path';

const RESULTS_DIR = path.join(
	process.env.DISTILL_HOME ?? `${process.env.HOME}/.distill`,
	'results'
);

export class ResultPathMissingError extends Error {
	constructor() {
		super('Result file not found on disk.');
		this.name = 'ResultPathMissingError';
	}
}

export class UnsafeResultPathError extends Error {
	constructor() {
		super('The result path is outside the job result directory.');
		this.name = 'UnsafeResultPathError';
	}
}

export interface ValidatedResultPath {
	jobDir: string;
	path: string;
	stats: Awaited<ReturnType<typeof stat>>;
}

function isWithin(root: string, candidate: string): boolean {
	const relative = path.relative(root, candidate);
	return relative === '' || (!relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative));
}

async function realPathOrMissing(filePath: string): Promise<string> {
	try {
		return await realpath(filePath);
	} catch {
		throw new ResultPathMissingError();
	}
}

/**
 * Ensure a result and every symlink below a directory result stay inside the
 * real job directory. The status file is data, so its output_path is never
 * trusted until it passes this check.
 */
async function rejectEscapingSymlinks(root: string, directory: string): Promise<void> {
	const entries = await readdir(directory, { withFileTypes: true });
	for (const entry of entries) {
		const entryPath = path.join(directory, entry.name);
		if (entry.isSymbolicLink()) {
			const target = await realPathOrMissing(entryPath);
			if (!isWithin(root, target)) throw new UnsafeResultPathError();
			continue;
		}
		if (entry.isDirectory()) await rejectEscapingSymlinks(root, entryPath);
	}
}

/** Validate and resolve a status output path beneath RESULTS_DIR/jobId. */
export async function validateResultPath(
	jobId: string,
	outputPath: string | undefined
): Promise<ValidatedResultPath> {
	if (!/^[A-Za-z0-9_-]+$/.test(jobId)) throw new UnsafeResultPathError();

	const resultsRoot = await realPathOrMissing(RESULTS_DIR);
	const lexicalJobDir = path.resolve(RESULTS_DIR, jobId);
	const jobDir = await realPathOrMissing(lexicalJobDir);
	if (!isWithin(resultsRoot, jobDir) || jobDir === resultsRoot) throw new UnsafeResultPathError();

	const lexicalOutput = outputPath
		? path.isAbsolute(outputPath)
			? path.resolve(outputPath)
			: path.resolve(lexicalJobDir, outputPath)
		: path.join(lexicalJobDir, 'output.jsonl');
	const resolvedOutput = await realPathOrMissing(lexicalOutput);
	if (!isWithin(jobDir, resolvedOutput)) throw new UnsafeResultPathError();

	const stats = await stat(resolvedOutput).catch(() => null);
	if (!stats) throw new ResultPathMissingError();
	if (!stats.isFile() && !stats.isDirectory()) throw new UnsafeResultPathError();
	if (stats.isDirectory()) await rejectEscapingSymlinks(jobDir, resolvedOutput);
	return { jobDir, path: resolvedOutput, stats };
}

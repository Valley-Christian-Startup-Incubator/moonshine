import { open } from 'node:fs/promises';
import path from 'node:path';
import { JOB_ID_PATTERN, RESULTS_DIR } from './moonshine-paths';
import { trainingSampleSchema, type TrainingExcerpt } from '../training-sample';

export async function readTrainingSample({
	jobId, runId, attemptStartedAt, canView
}: {
	jobId: string;
	runId: unknown;
	attemptStartedAt: string | undefined;
	canView: boolean;
}, now = Date.now()): Promise<TrainingExcerpt | null> {
	if (!canView || !JOB_ID_PATTERN.test(jobId) || typeof runId !== 'string') return null;
	let file;
	try {
		file = await open(path.join(RESULTS_DIR, jobId, 'training_sample.json'), 'r');
		const info = await file.stat();
		if (!info.isFile() || info.size > 4096) return null;
		const buffer = Buffer.alloc(4097);
		const { bytesRead } = await file.read(buffer, 0, buffer.length, 0);
		if (bytesRead > 4096) return null;
		const parsed = trainingSampleSchema.safeParse(JSON.parse(buffer.subarray(0, bytesRead).toString('utf8')));
		if (!parsed.success || parsed.data.runId !== runId) return null;
		const captured = Date.parse(parsed.data.capturedAt);
		if (captured > now || (attemptStartedAt && captured < Date.parse(attemptStartedAt))) return null;
		return parsed.data;
	} catch {
		return null;
	} finally {
		await file?.close();
	}
}

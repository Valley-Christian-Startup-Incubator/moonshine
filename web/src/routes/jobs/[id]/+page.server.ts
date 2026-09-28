import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';
import {
	getEvalReport,
	getGgufStatus,
	getJobAdvice,
	getJob,
	getJobDiagnosis,
	getJobDocuments,
	getJobLogTail,
	getGenerateQuality,
	isJobOwner,
	getQueueSummary,
	getTrainingCurve,
	listJobArtifacts
} from '$lib/server/queue';

export const load: PageServerLoad = async ({ params, locals }) => {
	const job = await getJob(params.id);
	if (!job) error(404, 'Job not found');
	const [logTail, diagnosis, artifacts, report, curve, queue, documents, gguf, advice, quality] = await Promise.all([
		getJobLogTail(job.id, 200),
		job.status === 'failed' ? getJobDiagnosis(job.id) : Promise.resolve(null),
		listJobArtifacts(job.id),
		job.type === 'train' ? getEvalReport(job.id) : Promise.resolve(null),
		job.type === 'train' ? getTrainingCurve(job.id) : Promise.resolve(null),
		getQueueSummary(),
		job.type === 'generate' && job.docNames.length ? getJobDocuments(job.id) : Promise.resolve(null),
		job.type === 'train' && job.status === 'completed' ? getGgufStatus(job.id) : Promise.resolve(null),
		job.type === 'train' ? getJobAdvice(job.id) : Promise.resolve(null),
		job.type === 'generate' ? getGenerateQuality(job.id, job.progress?.extra ?? {}) : Promise.resolve(null)
	]);
	// keep the payload small: examples are shown from the report, curve is downsampled
	const thin = (pts: [number, number][], max = 300) =>
		pts.length <= max ? pts : pts.filter((_, i) => i % Math.ceil(pts.length / max) === 0 || i === pts.length - 1);
	return {
		job,
		logTail,
		diagnosis,
		artifacts,
		report: report?.json ?? null,
		curve: curve
			? { train: thin(curve.train), valid: thin(curve.valid), totalSteps: curve.totalSteps, best: curve.best, settings: curve.settings }
			: null,
		queue,
		documents,
		gguf,
		advice,
		quality,
		isOwner: isJobOwner(job, locals.userName)
	};
};

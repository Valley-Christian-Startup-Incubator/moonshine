import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';
import {
	getEvalReport,
	getJob,
	getJobDiagnosis,
	getJobLogTail,
	getQueueSummary,
	getTrainingCurve,
	listJobArtifacts
} from '$lib/server/queue';

export const load: PageServerLoad = async ({ params }) => {
	const job = await getJob(params.id);
	if (!job) error(404, 'Job not found');
	const [logTail, diagnosis, artifacts, report, curve, queue] = await Promise.all([
		getJobLogTail(job.id, 200),
		job.status === 'failed' ? getJobDiagnosis(job.id) : Promise.resolve(null),
		listJobArtifacts(job.id),
		job.type === 'train' ? getEvalReport(job.id) : Promise.resolve(null),
		job.type === 'train' ? getTrainingCurve(job.id) : Promise.resolve(null),
		getQueueSummary()
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
		curve: curve ? { train: thin(curve.train), valid: thin(curve.valid), totalSteps: curve.totalSteps } : null,
		queue
	};
};

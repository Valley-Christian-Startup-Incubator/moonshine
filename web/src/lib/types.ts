/** Shared (client-safe) types for Moonshine v2. */
export type JobStatus = 'queued' | 'running' | 'paused' | 'completed' | 'failed' | 'cancelled';
export type JobType = 'generate' | 'train';

export const JOB_TYPE_LABELS: Record<JobType, string> = {
	generate: 'Generate Questions',
	train: 'Train + evaluate'
};

export const STATUS_LABELS: Record<JobStatus, string> = {
	queued: 'Queued',
	running: 'Running',
	paused: 'Paused',
	completed: 'Completed',
	failed: 'Failed',
	cancelled: 'Cancelled'
};

/** Measured on the Mac Studio (Sept 2026): the teacher writes ~2–2.5 s per item. */
export const SECONDS_PER_GENERATED_ITEM = 2.25;

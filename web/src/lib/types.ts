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
/** Grounded in source documents (a document section in every request): ~7 s per item. */
export const SECONDS_PER_GROUNDED_ITEM = 7;
/**
 * Answer check (the Teacher re-solves every item, plus replacements for dropped ones), as a multiplier range.
 * Calibrated Sept 27 2026 on the Mac Studio: plain AP Calc run 25 kept / 29 checked, generation ~2.35 s + check
 * ~2.5 s per item with a 14% drop rate (~2.4x); grounded FRC manual run 15.4 s per item vs 7 s unchecked (~2.2x).
 */
export const ANSWER_CHECK_OVERHEAD: [number, number] = [2.2, 2.7];

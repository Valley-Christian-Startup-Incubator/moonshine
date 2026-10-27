import { z } from 'zod';
import type { JobStatus } from './types';

export const trainingSampleSchema = z.object({
	version: z.literal(1),
	runId: z.uuid(),
	capturedAt: z.iso.datetime({ offset: true }),
	step: z.number().int().positive(),
	intervalSeconds: z.number().min(5).max(3600),
	tokenCount: z.number().int().min(1).max(64),
	text: z.string().min(1).max(512).refine((text) => text.trim().length > 0)
});

export type TrainingExcerpt = z.infer<typeof trainingSampleSchema>;
export type TrainingExcerptState = 'hidden' | 'unavailable' | 'recent' | 'stale' | 'paused' | 'queued' | 'finished';

export function trainingExcerptState({
	sample, status, phase, isOwner, now, updatesDelayed, pendingAction
}: {
	sample: TrainingExcerpt | null;
	status: JobStatus;
	phase: string | null;
	isOwner: boolean;
	now: number;
	updatesDelayed: boolean;
	pendingAction: boolean;
}): TrainingExcerptState {
	if (!isOwner) return 'hidden';
	if (!sample) return 'unavailable';
	if (status === 'queued') return 'queued';
	if (status === 'paused' || pendingAction) return 'paused';
	if (status !== 'running' || phase !== 'train') return 'finished';
	if (updatesDelayed || now - Date.parse(sample.capturedAt) > Math.max(15, sample.intervalSeconds * 3) * 1000) return 'stale';
	return 'recent';
}

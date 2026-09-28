import type { JobStatus } from './types';

export function statusBadgeClass(status: JobStatus | string): string {
	switch (status) {
		case 'queued':
			return 'badge badge-queued';
		case 'running':
			return 'badge badge-running';
		case 'paused':
			return 'badge badge-paused';
		case 'completed':
			return 'badge badge-success';
		case 'failed':
			return 'badge badge-failed';
		default:
			return 'badge badge-neutral';
	}
}

export function formatDuration(startedAt?: string, completedAt?: string): string {
	if (!startedAt) return '—';
	const start = new Date(startedAt).getTime();
	const end = completedAt ? new Date(completedAt).getTime() : Date.now();
	return formatSeconds(Math.max(0, Math.floor((end - start) / 1000)));
}

export function formatSeconds(secs: number): string {
	if (secs < 60) return `${Math.round(secs)}s`;
	const mins = Math.floor(secs / 60);
	if (mins < 60) return `${mins}m ${Math.round(secs % 60)}s`;
	const hrs = Math.floor(mins / 60);
	return `${hrs}h ${mins % 60}m`;
}

/** "about 1 h 20 min left" style estimate. */
export function formatEta(sec: number | null | undefined): string {
	if (sec === null || sec === undefined || !Number.isFinite(sec)) return '';
	const s = Math.max(0, Math.round(sec));
	const h = Math.floor(s / 3600);
	const m = Math.floor((s % 3600) / 60);
	if (h > 0) return `about ${h} h ${m} min left`;
	if (m > 0) return `about ${m} min left`;
	return 'under a minute left';
}

/** "about 1 h 50 min" (no "left") for up-front estimates. */
export function formatEstimate(sec: number): string {
	const s = Math.max(0, Math.round(sec));
	const h = Math.floor(s / 3600);
	const m = Math.max(1, Math.round((s % 3600) / 60));
	return h > 0 ? `about ${h} h ${m} min` : `about ${m} min`;
}

/** "about 1 h 30 min – 1 h 45 min" range for up-front estimates. */
export function formatEstimateRange(loSec: number, hiSec: number): string {
	const part = (sec: number) => {
		const s = Math.max(0, Math.round(sec));
		const h = Math.floor(s / 3600);
		const m = Math.max(1, Math.round((s % 3600) / 60));
		return h > 0 ? (m === 60 ? `${h + 1} h 0 min` : `${h} h ${m} min`) : m === 60 ? '1 h 0 min' : `${m} min`;
	};
	const a = part(loSec);
	const b = part(hiSec);
	return a === b ? `about ${a}` : `about ${a} – ${b}`;
}

export function formatTimestamp(iso?: string | null): string {
	if (!iso) return '—';
	return new Date(iso).toLocaleString(undefined, {
		month: 'short',
		day: 'numeric',
		hour: 'numeric',
		minute: '2-digit'
	});
}

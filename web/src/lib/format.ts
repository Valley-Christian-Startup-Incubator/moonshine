import type { JobStatus } from './types';

export function statusBadgeClass(status: JobStatus): string {
	switch (status) {
		case 'queued':
			return 'badge badge-queued';
		case 'running':
			return 'badge badge-running';
		case 'complete':
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
	const secs = Math.max(0, Math.floor((end - start) / 1000));
	if (secs < 60) return `${secs}s`;
	const mins = Math.floor(secs / 60);
	if (mins < 60) return `${mins}m ${secs % 60}s`;
	const hrs = Math.floor(mins / 60);
	return `${hrs}h ${mins % 60}m`;
}

export function formatTimestamp(iso?: string): string {
	if (!iso) return '—';
	return new Date(iso).toLocaleString(undefined, {
		month: 'short',
		day: 'numeric',
		hour: '2-digit',
		minute: '2-digit'
	});
}

export function formatBytes(bytes: number): string {
	if (bytes < 1024) return `${bytes} B`;
	if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
	if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
	return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

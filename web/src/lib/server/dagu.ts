import { DAGU_BASE_URL, DAGU_USER, DAGU_PASSWORD } from './env';

/**
 * Thin server-side client for Dagu's REST API.
 *
 * These paths target the v2 REST API bundled with the Dagu 1.17.x release
 * pinned by setup.sh. This is the only file that talks to Dagu directly.
 */

const API_BASE = '/api/v2';

function authHeader(): string {
	return 'Basic ' + Buffer.from(`${DAGU_USER}:${DAGU_PASSWORD}`).toString('base64');
}

async function daguFetch(path: string, init: RequestInit = {}): Promise<Response> {
	const res = await fetch(`${DAGU_BASE_URL}${path}`, {
		...init,
		headers: {
			Authorization: authHeader(),
			'Content-Type': 'application/json',
			...(init.headers ?? {})
		}
	});
	return res;
}

export interface DaguDagRunSummary {
	name: string;
	dagRunId: string;
	status: number;
	statusLabel: string;
	startedAt?: string;
	finishedAt?: string;
	params?: string;
	queuedAt?: string;
}

/** DAG run status codes as reported by Dagu. */
export const DAGU_STATUS = {
	NOT_STARTED: 0,
	RUNNING: 1,
	FAILED: 2,
	CANCELLED: 3,
	SUCCESS: 4,
	QUEUED: 5
} as const;

export async function isDaguHealthy(): Promise<boolean> {
	try {
		const res = await daguFetch(`${API_BASE}/health`);
		return res.ok;
	} catch {
		return false;
	}
}

/** Enqueue a DAG run on the shared `mac-studio` global queue with the given params. */
export async function enqueueDag(
	dagName: string,
	params: Record<string, string | number>
): Promise<{ dagRunId: string }> {
	const paramString = Object.entries(params)
		.map(([k, v]) => `${k}="${String(v).replace(/(["\\])/g, '\\$1')}"`)
		.join(' ');

	const res = await daguFetch(`${API_BASE}/dags/${encodeURIComponent(dagName)}/enqueue`, {
		method: 'POST',
		body: JSON.stringify({ params: paramString })
	});

	if (!res.ok) {
		const text = await res.text().catch(() => '');
		throw new Error(`Dagu enqueue failed for ${dagName}: ${res.status} ${text}`);
	}

	const data = (await res.json().catch(() => ({}))) as { dagRunId?: string; requestId?: string };
	return { dagRunId: data.dagRunId ?? data.requestId ?? '' };
}

/** List recent dag-runs across all templates (used by /jobs and /admin dashboards). */
export async function listDagRuns(): Promise<DaguDagRunSummary[]> {
	const res = await daguFetch(`${API_BASE}/dag-runs`);
	if (!res.ok) return [];
	const data = (await res.json().catch(() => ({ dagRuns: [] }))) as {
		dagRuns?: DaguDagRunSummary[];
	};
	return data.dagRuns ?? [];
}

export async function getDagRunStatus(
	dagName: string,
	dagRunId: string
): Promise<DaguDagRunSummary | null> {
	const res = await daguFetch(
		`${API_BASE}/dag-runs/${encodeURIComponent(dagName)}/${encodeURIComponent(dagRunId)}`
	);
	if (!res.ok) return null;
	const data = (await res.json().catch(() => null)) as {
		dagRunDetails?: DaguDagRunSummary;
	} | null;
	return data?.dagRunDetails ?? null;
}

export async function cancelDagRun(dagName: string, dagRunId: string): Promise<boolean> {
	const res = await daguFetch(
		`${API_BASE}/dag-runs/${encodeURIComponent(dagName)}/${encodeURIComponent(dagRunId)}/stop`,
		{ method: 'POST' }
	);
	return res.ok;
}

/** Position (1-indexed) of a queued run within the mac-studio global queue, if queued. */
export async function getQueuePosition(dagRunId: string): Promise<number | null> {
	const res = await daguFetch(`${API_BASE}/dag-runs?status=${DAGU_STATUS.QUEUED}`);
	if (!res.ok) return null;
	const data = (await res.json().catch(() => ({ dagRuns: [] }))) as {
		dagRuns?: DaguDagRunSummary[];
	};
	const queuedRuns = (data.dagRuns ?? []).sort((a, b) =>
		(a.queuedAt ?? '').localeCompare(b.queuedAt ?? '')
	);
	const idx = queuedRuns.findIndex((run) => run.dagRunId === dagRunId);
	return idx === -1 ? null : idx + 1;
}

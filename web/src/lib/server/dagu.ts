import { DAGU_BASE_URL, DAGU_USER, DAGU_PASSWORD } from './env';

/**
 * Thin server-side client for Dagu's REST API.
 *
 * Dagu's API surface has shifted across releases (v1 -> v2). The paths below
 * target the v1 REST API documented at https://docs.dagu.cloud/reference/rest-api
 * as of Dagu ~1.14. If `setup.sh` pins a different Dagu release, verify these
 * paths against `dagu` --help / the bundled OpenAPI spec and adjust here —
 * this is the only file that talks to Dagu directly.
 */

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
		const res = await daguFetch('/api/v1/health');
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
		.map(([k, v]) => `${k}=${v}`)
		.join(' ');

	const res = await daguFetch(`/api/v1/dags/${dagName}/start`, {
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
	const res = await daguFetch('/api/v1/dag-runs');
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
	const res = await daguFetch(`/api/v1/dags/${dagName}/dag-runs/${dagRunId}`);
	if (!res.ok) return null;
	return (await res.json().catch(() => null)) as DaguDagRunSummary | null;
}

export async function cancelDagRun(dagName: string, dagRunId: string): Promise<boolean> {
	const res = await daguFetch(`/api/v1/dags/${dagName}/dag-runs/${dagRunId}/stop`, {
		method: 'POST'
	});
	return res.ok;
}

/** Position (1-indexed) of a queued run within the mac-studio global queue, if queued. */
export async function getQueuePosition(dagRunId: string): Promise<number | null> {
	const res = await daguFetch('/api/v1/queues/mac-studio');
	if (!res.ok) return null;
	const data = (await res.json().catch(() => ({ items: [] }))) as {
		items?: { dagRunId: string }[];
	};
	const idx = (data.items ?? []).findIndex((i) => i.dagRunId === dagRunId);
	return idx === -1 ? null : idx + 1;
}

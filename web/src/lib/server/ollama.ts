import { OLLAMA_REGISTRY_FILE, OLLAMA_URL, readJsonFile, writeJsonAtomic } from './moonshine-paths';

export interface OllamaModelSummary {
	name: string;
	size: number;
	details?: { parameter_size?: string; quantization_level?: string; format?: string };
}

export interface RegisteredModel {
	tag: string;
	owner: string | null;
	jobId: string;
	student: string;
	createdAt: string;
}

export async function listOllamaModels(): Promise<OllamaModelSummary[]> {
	const res = await fetch(`${OLLAMA_URL}/api/tags`, { signal: AbortSignal.timeout(10_000) });
	if (!res.ok) throw new Error(`Ollama /api/tags failed: ${res.status}`);
	const data = (await res.json()) as { models?: OllamaModelSummary[] };
	return data.models ?? [];
}

/** Models the app registered (fine-tuned students), optionally for one owner. */
export async function listRegisteredModels(owner?: string): Promise<RegisteredModel[]> {
	const reg = await readJsonFile<RegisteredModel[]>(OLLAMA_REGISTRY_FILE, []);
	return owner === undefined ? reg : reg.filter((r) => r.owner === owner);
}

/** Delete an Ollama model the app registered. Base tags are never deleted. */
export async function deleteRegisteredModel(tag: string): Promise<boolean> {
	const reg = await readJsonFile<RegisteredModel[]>(OLLAMA_REGISTRY_FILE, []);
	if (!reg.some((r) => r.tag === tag)) throw new Error(`Refusing to delete ${tag}: not registered by Moonshine`);
	const res = await fetch(`${OLLAMA_URL}/api/delete`, {
		method: 'DELETE',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ model: tag }),
		signal: AbortSignal.timeout(60_000)
	}).catch(() => null);
	const ok = Boolean(res && (res.ok || res.status === 404));
	if (ok) {
		const latest = await readJsonFile<RegisteredModel[]>(OLLAMA_REGISTRY_FILE, []);
		await writeJsonAtomic(
			OLLAMA_REGISTRY_FILE,
			latest.filter((r) => r.tag !== tag)
		);
	}
	return ok;
}

function paramCount(size: string | undefined): number | null {
	const m = /([\d.]+)\s*([KMBT])/i.exec(size ?? '');
	if (!m) return null;
	return Number(m[1]) * { K: 1e3, M: 1e6, B: 1e9, T: 1e12 }[m[2].toUpperCase() as 'K' | 'M' | 'B' | 'T'];
}

async function showParamCount(name: string): Promise<number | null> {
	try {
		const res = await fetch(`${OLLAMA_URL}/api/show`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ model: name }),
			signal: AbortSignal.timeout(10_000)
		});
		if (!res.ok) return null;
		const info = (await res.json()) as { model_info?: Record<string, unknown> };
		for (const [k, v] of Object.entries(info.model_info ?? {})) {
			if (k.endsWith('parameter_count') && typeof v === 'number') return v;
		}
	} catch {
		/* ignore */
	}
	return null;
}

let teacherCache: { name: string; at: number } | null = null;

/**
 * The teacher is the largest Ollama model by parameter count (currently
 * qwen3.8:27b-mlx). TEACHER_MODEL in ~/.distill/env overrides it ("auto" = pick).
 * Models Moonshine registered itself are never chosen.
 */
export async function getTeacherModel(): Promise<string> {
	const configured = process.env.TEACHER_MODEL;
	if (configured && configured.toLowerCase() !== 'auto') return configured;
	if (teacherCache && Date.now() - teacherCache.at < 5 * 60_000) return teacherCache.name;
	const fallback = 'qwen3.8:27b-mlx';
	try {
		const own = new Set((await listRegisteredModels()).map((r) => r.tag));
		const models = (await listOllamaModels()).filter(
			(m) => !own.has(m.name) && !own.has(m.name.replace(/:latest$/, ''))
		);
		let best: { name: string; n: number } | null = null;
		for (const m of models) {
			const n = paramCount(m.details?.parameter_size) ?? (await showParamCount(m.name)) ?? m.size / 0.6;
			if (!best || n > best.n) best = { name: m.name, n };
		}
		const name = best?.name ?? fallback;
		teacherCache = { name, at: Date.now() };
		return name;
	} catch {
		return fallback;
	}
}

export interface ChatMessage {
	role: 'system' | 'user' | 'assistant';
	content: string;
}

/** One non-streaming chat turn with the teacher through the Ollama HTTP API (thinking off). */
export async function teacherChat(
	messages: ChatMessage[],
	options: { temperature?: number; maxTokens?: number; timeoutMs?: number; model?: string } = {}
): Promise<{ content: string; model: string }> {
	const model = options.model ?? (await getTeacherModel());
	const res = await fetch(`${OLLAMA_URL}/api/chat`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({
			model,
			messages,
			stream: false,
			think: false,
			keep_alive: '15m',
			options: { temperature: options.temperature ?? 0.6, num_predict: options.maxTokens ?? 4096, num_ctx: 32768 }
		}),
		signal: AbortSignal.timeout(options.timeoutMs ?? 300_000)
	});
	if (!res.ok) throw new Error(`Ollama chat failed: ${res.status} ${await res.text().catch(() => '')}`);
	const data = (await res.json()) as { message?: { content?: string } };
	const content = (data.message?.content ?? '').replace(/<think>[\s\S]*?<\/think>/g, '').trim();
	return { content, model };
}

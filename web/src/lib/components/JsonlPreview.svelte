<script lang="ts">
	import type { PreviewRow } from '$lib/server/jsonl-preview';
	let { jobId, artifacts, selected = $bindable('questions') }: {
		jobId: string;
		artifacts: { key: string; label: string }[];
		selected?: string;
	} = $props();
	const activeKey = $derived(artifacts.some((a) => a.key === selected) ? selected : artifacts[0]?.key ?? 'questions');
	let rows: PreviewRow[] = $state([]);
	let index = $state(0);
	let nextOffset: number | null = $state(null);
	let loading = $state(false);
	let failure = $state('');
	let request = 0;
	let raw = $state(false);
	const current = $derived(rows[index]);
	const fields = $derived(current && !current.invalid && current.value !== null && typeof current.value === 'object' && !Array.isArray(current.value)
		? Object.entries(current.value as Record<string, unknown>) : []);
	const labels: Record<string, string> = { prompt: 'Question', completion: 'Answer', question: 'Question', answer: 'Answer', messages: 'Messages', text: 'Text', reason: 'Reason', doc: 'Document' };
	const display = (value: unknown) => typeof value === 'string' ? value : JSON.stringify(value, null, 2);

	async function load(append = false) {
		const token = ++request;
		loading = true;
		failure = '';
		if (!append) { rows = []; index = 0; nextOffset = null; raw = false; }
		try {
			const response = await fetch(`/jobs/${encodeURIComponent(jobId)}/preview?file=${encodeURIComponent(activeKey)}&offset=${append ? nextOffset ?? 0 : 0}`);
			if (response.redirected) throw new Error('Your session has expired. Reload the page to sign in.');
			const body = await response.json();
			if (!response.ok) throw new Error(body.message || 'Could not load this preview.');
			if (token !== request) return;
			const previousLength = rows.length;
			rows = append ? [...rows, ...body.rows] : body.rows;
			nextOffset = body.nextOffset;
			if (append && rows.length > previousLength) index = previousLength;
		} catch (cause) {
			if (token === request) failure = cause instanceof Error ? cause.message : 'Could not load this preview.';
		} finally {
			if (token === request) loading = false;
		}
	}
	$effect(() => {
		activeKey; jobId;
		load();
		return () => { request++; };
	});
</script>

<section id="jsonl-preview" class="card mt-5 min-w-0 scroll-mt-5 p-5" aria-labelledby="preview-heading">
	<div class="flex flex-wrap items-center justify-between gap-3">
		<h2 id="preview-heading" class="text-base font-semibold text-zinc-100">Preview generated files</h2>
		<a class="text-sm text-zinc-300 underline hover:text-white" href="/jobs/{jobId}/download?file={activeKey}" download>Download file</a>
	</div>
	<div class="mt-3 flex flex-wrap gap-2" aria-label="File to preview">
		{#each artifacts as artifact (artifact.key)}
			<button type="button" class="btn-secondary min-h-11" aria-pressed={activeKey === artifact.key} onclick={() => { selected = artifact.key; }}>{artifact.label.replace(' (.jsonl)', '')}</button>
		{/each}
	</div>
	<div class="mt-4" aria-live="polite" aria-busy={loading}>
		{#if failure}
			<p class="text-sm text-amber-200">{failure}</p>
			<button type="button" class="btn-secondary mt-2" onclick={() => load(rows.length > 0)}>Retry</button>
		{:else if loading && !current}
			<p class="py-6 text-sm text-zinc-400">Loading preview…</p>
		{:else if !current}
			<p class="py-6 text-sm text-zinc-400">{nextOffset === null ? 'No records yet.' : 'No records in this batch.'}</p>
			{#if nextOffset !== null}<button type="button" class="btn-secondary" onclick={() => load(true)}>Next batch</button>{/if}
		{/if}
		{#if current}
			<div class="flex flex-wrap items-center justify-between gap-3">
				<p class="text-sm text-zinc-400">{activeKey === 'questions' ? 'Question' : 'Record'} {index + 1}</p>
				<div class="flex gap-2">
					<button type="button" class="btn-secondary min-h-11" disabled={index === 0 || loading} onclick={() => { index--; raw = false; }}>Previous</button>
					<button type="button" class="btn-secondary min-h-11" disabled={loading || (index === rows.length - 1 && nextOffset === null)} onclick={() => { raw = false; if (index < rows.length - 1) index++; else load(true); }}>{loading ? 'Loading…' : 'Next'}</button>
				</div>
			</div>
			{#if current.invalid}
				<p class="mt-4 text-sm text-amber-200">This entry contains invalid JSON. Its original text is shown below.</p>
				<pre class="preview-text mt-3 rounded-md bg-black/30 p-4 text-sm text-zinc-300">{current.raw}</pre>
			{:else}
				<dl class="mt-4 space-y-5">
					{#each fields as [key, value]}
						<div>
							<dt class="text-sm font-semibold text-zinc-400">{labels[key] ?? key.replaceAll('_', ' ')}</dt>
							<dd class="preview-text mt-1 text-sm leading-6 text-zinc-200">{display(value)}</dd>
						</div>
					{/each}
				</dl>
				{#if !fields.length}<pre class="preview-text mt-4 text-sm text-zinc-200">{display(current.value)}</pre>{/if}
				<details class="mt-5" bind:open={raw}>
					<summary class="cursor-pointer text-sm text-zinc-400">Raw JSON</summary>
					<pre class="preview-text mt-2 max-h-96 overflow-y-auto rounded-md bg-black/30 p-3 text-xs text-zinc-300">{JSON.stringify(current.value, null, 2)}</pre>
				</details>
			{/if}
		{/if}
	</div>
</section>

<style>
	.preview-text { white-space: pre-wrap; overflow-wrap: anywhere; }
	button[aria-pressed='true'] { background: #3f3f46; color: #fff; }
</style>

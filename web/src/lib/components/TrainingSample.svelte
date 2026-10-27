<script lang="ts">
	import { formatTimestamp } from '$lib/format';
	import type { JobStatus } from '$lib/types';
	import type { TrainingExcerpt, TrainingExcerptState } from '$lib/training-sample';

	let { sample, state, now, status, updatesDelayed }: {
		sample: TrainingExcerpt | null;
		state: TrainingExcerptState;
		now: number;
		status: JobStatus;
		updatesDelayed: boolean;
	} = $props();
	const age = $derived(sample ? Math.max(0, Math.floor((now - Date.parse(sample.capturedAt)) / 1000)) : 0);
	const ageLabel = $derived(age < 60 ? `${age} s ago` : `${Math.floor(age / 60)} min ago`);
	const label = $derived.by(() => {
		switch (state) {
			case 'hidden': return 'Owner view';
			case 'unavailable': return status === 'running' ? 'Sample unavailable' : 'No sample recorded';
			case 'recent': return 'Recent';
			case 'stale': return updatesDelayed ? 'Updates delayed' : 'Waiting for another sample';
			case 'paused': return status === 'paused' ? 'Paused · last sample' : 'Control requested · last sample';
			case 'queued': return 'Queued · last sample';
			case 'finished': return status === 'failed' || status === 'cancelled' ? 'Stopped · last sample' : 'Training finished · last sample';
		}
	});
</script>

<section class="card mt-4 p-4" aria-label="Recent training sample">
	<div class="flex flex-wrap items-center justify-between gap-2">
		<h2 class="text-xs uppercase tracking-[0.16em] text-zinc-400">Recent training sample</h2>
		<span class="rounded border px-2 py-1 text-xs" class:border-emerald-900={state === 'recent'} class:text-emerald-300={state === 'recent'} class:border-zinc-700={state !== 'recent'} class:text-zinc-400={state !== 'recent'}>{label}</span>
	</div>
	<span class="sr-only" aria-live="polite">{label}</span>
	{#if state === 'hidden'}
		<p class="mt-3 text-sm text-zinc-400">Excerpts are shown for the selected job owner. Names share a classroom sign-in; this is not private access.</p>
	{:else if sample}
		<p class="mt-3 text-xs text-zinc-400">Step {sample.step.toLocaleString()} · {sample.tokenCount} tokens · captured <time datetime={sample.capturedAt} title={formatTimestamp(sample.capturedAt)}>{ageLabel}</time> · sampled about every {sample.intervalSeconds} s</p>
		<div class="mt-3 max-h-32 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-black/40 p-3 font-mono text-sm leading-6 text-zinc-300" class:opacity-60={state !== 'recent'} tabindex="0" role="textbox" aria-readonly="true" aria-multiline="true" aria-label="Recent training sample text">{sample.text}</div>
		<p class="mt-2 text-xs leading-5 text-zinc-500">Start of one example in a batch the Student trained on. This is a saved excerpt, not a live view of each token.</p>
	{:else}
		<p class="mt-3 text-sm text-zinc-400">{status === 'running' ? 'No excerpt is available for this training attempt yet. Capture may be disabled or unavailable. The globe uses illustrative phrases.' : 'The globe uses illustrative phrases when no training excerpt is available.'}</p>
	{/if}
</section>

<script lang="ts">
	import { page } from '$app/stores';
	import { invalidateAll, replaceState } from '$app/navigation';
	import type { PageData } from './$types';
	import { statusBadgeClass, formatDuration, formatTimestamp } from '$lib/format';
	import { pushToast } from '$lib/toast.svelte';

	let { data }: { data: PageData } = $props();

	$effect(() => {
		if ($page.url.searchParams.get('submitted') === '1') {
			pushToast(`Job ${data.job.id} submitted.`, 'success');
			const url = new URL($page.url);
			url.searchParams.delete('submitted');
			replaceState(url, {});
		}
	});

	$effect(() => {
		if (data.job.status === 'queued' || data.job.status === 'running') {
			const interval = setInterval(() => {
				invalidateAll();
			}, 5000);
			return () => clearInterval(interval);
		}
	});
</script>

<svelte:head>
	<title>Job {data.job.id} — Distill Scheduler</title>
</svelte:head>

<div class="flex items-center justify-between">
	<div>
		<div class="flex items-center gap-3">
			<h1 class="font-mono text-lg font-semibold text-zinc-100">{data.job.id}</h1>
			<span class={statusBadgeClass(data.job.status)}>{data.job.status}</span>
		</div>
		<p class="mt-1 text-sm text-zinc-400">{data.job.team} · {data.job.type}</p>
	</div>
	{#if data.job.status === 'complete'}
		<a href="/jobs/{data.job.id}/download" class="btn-primary">Download result</a>
	{/if}
</div>

<div class="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2">
	<div class="card p-4">
		<h2 class="text-xs font-medium uppercase tracking-wide text-zinc-500">Timing</h2>
		<dl class="mt-3 space-y-2 text-sm">
			<div class="flex justify-between">
				<dt class="text-zinc-500">Submitted</dt>
				<dd class="text-zinc-300">{formatTimestamp(data.job.submittedAt)}</dd>
			</div>
			<div class="flex justify-between">
				<dt class="text-zinc-500">Started</dt>
				<dd class="text-zinc-300">{formatTimestamp(data.job.startedAt)}</dd>
			</div>
			<div class="flex justify-between">
				<dt class="text-zinc-500">Completed</dt>
				<dd class="text-zinc-300">{formatTimestamp(data.job.completedAt)}</dd>
			</div>
			<div class="flex justify-between">
				<dt class="text-zinc-500">Duration</dt>
				<dd class="font-mono text-xs text-zinc-300">
					{formatDuration(data.job.startedAt, data.job.completedAt)}
				</dd>
			</div>
			{#if data.job.status === 'queued' && data.job.queuePosition}
				<div class="flex justify-between">
					<dt class="text-zinc-500">Queue position</dt>
					<dd class="text-zinc-300">#{data.job.queuePosition}</dd>
				</div>
			{/if}
		</dl>
	</div>

	<div class="card p-4">
		<h2 class="text-xs font-medium uppercase tracking-wide text-zinc-500">Parameters</h2>
		<dl class="mt-3 space-y-2 text-sm">
			<div class="flex justify-between">
				<dt class="text-zinc-500">Input file</dt>
				<dd class="truncate font-mono text-xs text-zinc-300">{data.job.inputFile}</dd>
			</div>
			{#each Object.entries(data.job.params) as [key, value] (key)}
				<div class="flex justify-between">
					<dt class="text-zinc-500">{key}</dt>
					<dd class="font-mono text-xs text-zinc-300">{value}</dd>
				</div>
			{/each}
		</dl>
	</div>
</div>

{#if data.job.status === 'failed' && data.job.error}
	<div class="mt-6 rounded-md border border-red-800 bg-red-950 px-4 py-3 text-sm text-red-300">
		{data.job.error}
	</div>
{/if}

{#if data.job.status === 'running' || data.job.status === 'failed'}
	<div class="card mt-6 p-4">
		<h2 class="text-xs font-medium uppercase tracking-wide text-zinc-500">Log tail</h2>
		<pre class="mt-3 max-h-96 overflow-auto whitespace-pre-wrap rounded-md bg-black p-3 font-mono text-xs text-zinc-400">{data.log ||
				'No log output yet.'}</pre>
	</div>
{/if}

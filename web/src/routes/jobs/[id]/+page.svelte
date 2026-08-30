<script lang="ts">
	import { page } from '$app/stores';
	import { invalidateAll, replaceState } from '$app/navigation';
	import { enhance } from '$app/forms';
	import type { ActionData, PageData } from './$types';
	import { statusBadgeClass, formatDuration, formatTimestamp } from '$lib/format';
	import { JOB_TYPES, JOB_TYPE_FIELDS } from '$lib/types';
	import { pushToast } from '$lib/toast.svelte';

	let { data, form }: { data: PageData; form: ActionData } = $props();
	let retrying = $state(false);
	const jobType = $derived(JOB_TYPES.find((candidate) => candidate.value === data.job.type)!);

	function parameterLabel(name: string): string {
		return JOB_TYPE_FIELDS[data.job.type].find((field) => field.name === name)?.label ?? name;
	}

	$effect(() => {
		if ($page.url.searchParams.get('submitted') === '1') {
			pushToast(`Job ${data.job.id} submitted.`, 'success');
			const url = new URL($page.url);
			url.searchParams.delete('submitted');
			replaceState(url, {});
		}
	});

	$effect(() => {
		// Keep polling briefly after a failure too: diagnose_job.py runs inside
		// the failure handler and can take up to ~2 minutes to write diagnosis.md.
		const awaitingDiagnosis = data.job.status === 'failed' && !data.diagnosis.diagnosisMarkdown;
		if (data.job.status === 'queued' || data.job.status === 'running' || awaitingDiagnosis) {
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
		<p class="mt-1 text-sm text-zinc-400">{data.job.team} · {jobType.label}</p>
	</div>
	{#if data.job.status === 'complete'}
		<a href="/jobs/{data.job.id}/download" class="btn-primary">Download result</a>
	{/if}
</div>

{#if data.job.status === 'queued'}
	<div class="mt-5 rounded-lg border border-blue-900 bg-blue-950/40 px-4 py-3 text-sm text-blue-100">
		This job is waiting for the Mac Studio. You can close this page and return later.
		{#if data.job.queuePosition} It is number {data.job.queuePosition} in line.{/if}
	</div>
{:else if data.job.status === 'running'}
	<div class="mt-5 rounded-lg border border-amber-900 bg-amber-950/40 px-4 py-3 text-sm text-amber-100">
		This job is running. The page refreshes automatically, and you can safely leave and return later.
	</div>
{:else if data.job.status === 'complete'}
	<div class="mt-5 rounded-lg border border-emerald-900 bg-emerald-950/30 px-4 py-3 text-sm text-emerald-100">
		<p class="font-medium">Your result is ready.</p>
		<p class="mt-1 text-emerald-200/80">{jobType.output} {jobType.nextStep}</p>
	</div>
{/if}

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
		<h2 class="text-xs font-medium uppercase tracking-wide text-zinc-500">Job settings</h2>
		<dl class="mt-3 space-y-2 text-sm">
			{#if jobType.requiresInput}
				<div class="flex justify-between gap-4">
					<dt class="text-zinc-500">Input file</dt>
					<dd class="truncate font-mono text-xs text-zinc-300">{data.job.inputFile}</dd>
				</div>
			{:else}
				<div class="flex justify-between">
					<dt class="text-zinc-500">Input file</dt>
					<dd class="text-xs text-zinc-300">Not required</dd>
				</div>
			{/if}
			{#each Object.entries(data.job.params) as [key, value] (key)}
				<div class="flex justify-between">
					<dt class="text-zinc-500">{parameterLabel(key)}</dt>
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

{#if data.job.status === 'failed'}
	<div class="card mt-6 p-4">
		<h2 class="text-xs font-medium uppercase tracking-wide text-zinc-500">AI diagnosis</h2>
		<p class="mt-2 text-sm text-zinc-400">
			The diagnosis explains the likely cause. Suggested settings are a starting point, so review them before retrying.
		</p>
		{#if form?.error}
			<div class="mt-3 rounded-md border border-red-800 bg-red-950 px-3 py-2 text-xs text-red-300">
				{form.error}
			</div>
		{/if}
		{#if data.diagnosis.diagnosisMarkdown}
			<pre
				class="mt-3 max-h-96 overflow-auto whitespace-pre-wrap rounded-md bg-black p-3 font-mono text-xs text-zinc-300">{data
					.diagnosis.diagnosisMarkdown}</pre>
			{#if data.diagnosis.suggestedRetryParams && Object.keys(data.diagnosis.suggestedRetryParams).length > 0}
				<div class="mt-3 flex items-center justify-between gap-3 rounded-md border border-border bg-bg-subtle px-3 py-2">
					<div class="font-mono text-xs text-zinc-400">
						{JSON.stringify(data.diagnosis.suggestedRetryParams)}
					</div>
					<form
						method="POST"
						action="?/retry"
						use:enhance={() => {
							retrying = true;
							return async ({ update }) => {
								await update();
								retrying = false;
							};
						}}
					>
						<button type="submit" class="btn-secondary shrink-0" disabled={retrying}>
							{retrying ? 'Retrying…' : 'Retry with suggested params'}
						</button>
					</form>
				</div>
			{/if}
		{:else}
			<p class="mt-3 text-sm text-zinc-500">
				Waiting for automated diagnosis (runs headless right after failure, can take a couple
				minutes)…
			</p>
		{/if}
	</div>
{/if}

{#if data.job.status === 'running' || data.job.status === 'failed'}
	<div class="card mt-6 p-4">
		<h2 class="text-xs font-medium uppercase tracking-wide text-zinc-500">Log tail</h2>
		<pre class="mt-3 max-h-96 overflow-auto whitespace-pre-wrap rounded-md bg-black p-3 font-mono text-xs text-zinc-400">{data.log ||
				'No log output yet.'}</pre>
	</div>
{/if}

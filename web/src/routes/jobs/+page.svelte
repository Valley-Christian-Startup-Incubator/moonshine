<script lang="ts">
	import HowMoonshineWorks from '$lib/components/HowMoonshineWorks.svelte';
	import { invalidateAll } from '$app/navigation';
	import { page } from '$app/stores';
	import JobControls from '$lib/components/JobControls.svelte';
	import ProgressBar from '$lib/components/ProgressBar.svelte';
	import { formatTimestamp, statusBadgeClass } from '$lib/format';
	import { JOB_TYPE_LABELS, STATUS_LABELS, type JobStatus } from '$lib/types';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	let mineOnly = $state(false);
	let statusFilter = $state('all');

	const jobs = $derived(
		data.jobs.filter(
			(j) =>
				(!mineOnly || j.ownerName === $page.data.userName) && (statusFilter === 'all' || j.status === statusFilter)
		)
	);
	const pausedJob = $derived(data.jobs.find((j) => j.id === data.queue.pausedJobId));
	const active = $derived(data.jobs.some((j) => j.status === 'running' || j.status === 'queued' || j.pendingAction));

	$effect(() => {
		const t = setInterval(() => invalidateAll(), active ? 4000 : 20000);
		return () => clearInterval(t);
	});
</script>

<svelte:head><title>Jobs — Moonshine</title></svelte:head>

<HowMoonshineWorks />

<div class="flex flex-wrap items-center justify-between gap-3">
	<h1 class="text-2xl font-semibold text-zinc-100">Jobs</h1>
	<div class="flex flex-wrap items-center gap-3">
		<label class="flex items-center gap-2 text-sm text-zinc-400"><input type="checkbox" bind:checked={mineOnly} /> Only mine</label>
		<select aria-label="Status" bind:value={statusFilter} class="input w-auto text-sm">
			<option value="all">All statuses</option>
			{#each Object.entries(STATUS_LABELS) as [value, label] (value)}
				<option {value}>{label}</option>
			{/each}
		</select>
	</div>
</div>

<p class="mt-2 text-sm text-zinc-400">One job runs at a time, in the order submitted. You can close this page; jobs keep running.</p>

{#if !data.queue.runnerAlive}
	<div class="mt-4 rounded-lg border border-red-900 bg-red-950/40 px-4 py-3 text-sm text-red-200">
		The job runner on the Mac Studio is not responding, so queued jobs will not start. Ask an instructor to check it.
	</div>
{/if}

{#if pausedJob}
	<div class="mt-4 rounded-lg border border-violet-800 bg-violet-950/40 px-4 py-3 text-sm text-violet-100">
		<strong>The queue is on hold.</strong> <a class="underline" href="/jobs/{pausedJob.id}">{JOB_TYPE_LABELS[pausedJob.type]}</a>
		by {pausedJob.ownerName} is paused. No other job will start until it is resumed or cancelled.
		{#if data.queue.queuedJobIds.length}({data.queue.queuedJobIds.length} waiting){/if}
	</div>
{/if}

<div class="card mt-4 overflow-x-auto">
	<table class="w-full text-sm">
		<thead>
			<tr class="border-b border-border-subtle text-left text-xs uppercase tracking-wide text-zinc-500">
				<th class="px-4 py-3 font-medium">Type</th>
				<th class="px-4 py-3 font-medium">Owner</th>
				<th class="px-4 py-3 font-medium">Status</th>
				<th class="px-4 py-3 font-medium">Progress</th>
				<th class="px-4 py-3 font-medium">Submitted</th>
				<th class="px-4 py-3 font-medium"></th>
			</tr>
		</thead>
		<tbody>
			{#each jobs as job (job.id)}
				<tr class="border-b border-border-subtle last:border-0 hover:bg-bg-subtle/50">
					<td class="px-4 py-3">
						<div class="flex flex-wrap items-center gap-2">
							<a href="/jobs/{job.id}" class="font-medium text-zinc-100 hover:underline">{JOB_TYPE_LABELS[job.type]}</a>
							<a href="/jobs/{job.id}" class="btn-secondary px-2 py-1 text-xs" aria-label="Details for job {job.id}">Details</a>
						</div>
						<p class="font-mono text-xs text-zinc-500">
							{job.id}{#if job.type === 'train'} · {job.params.STUDENT}{/if}{#if job.type === 'generate'} · {job.params.COUNT} questions{/if}
						</p>
					</td>
					<td class="px-4 py-3 text-zinc-300">{job.ownerName}</td>
					<td class="px-4 py-3">
						<span class={statusBadgeClass(job.status)}>{STATUS_LABELS[job.status as JobStatus]}</span>
						{#if job.status === 'queued' && job.queuePosition}
							<p class="mt-1 text-xs text-zinc-500">#{job.queuePosition} in line</p>
						{/if}
					</td>
					<td class="px-4 py-3"><ProgressBar progress={job.progress} status={job.status} /></td>
					<td class="px-4 py-3 text-xs text-zinc-500">{formatTimestamp(job.submittedAt)}</td>
					<td class="px-4 py-3"><JobControls id={job.id} status={job.status} owner={job.ownerName} pending={job.pendingAction} compact /></td>
				</tr>
			{:else}
				<tr><td colspan="6" class="px-4 py-10 text-center text-sm text-zinc-500">No jobs yet. Start one in the <a href="/" class="underline">Lab</a>.</td></tr>
			{/each}
		</tbody>
	</table>
</div>

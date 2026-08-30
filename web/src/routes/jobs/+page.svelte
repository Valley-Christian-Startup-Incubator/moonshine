<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import type { PageData } from './$types';
	import { statusBadgeClass, formatDuration, formatTimestamp } from '$lib/format';
	import type { JobStatus } from '$lib/types';

	let { data }: { data: PageData } = $props();

	let teamFilter = $state<string>('all');
	let statusFilter = $state<string>('all');

	const statuses: JobStatus[] = ['queued', 'running', 'complete', 'failed'];

	const filteredJobs = $derived(
		data.jobs.filter((job) => {
			if (teamFilter !== 'all' && job.team !== teamFilter) return false;
			if (statusFilter !== 'all' && job.status !== statusFilter) return false;
			return true;
		})
	);

	function jobTypeLabel(value: string): string {
		return data.jobTypes.find((jobType) => jobType.value === value)?.label ?? value;
	}

	$effect(() => {
		const interval = setInterval(() => {
			invalidateAll();
		}, 10_000);
		return () => clearInterval(interval);
	});
</script>

<svelte:head>
	<title>Jobs — Distill Scheduler</title>
</svelte:head>

<div class="flex items-center justify-between">
	<h1 class="text-xl font-semibold text-zinc-100">Jobs</h1>
	<div class="flex gap-2">
		<select bind:value={teamFilter} class="input w-auto text-sm">
			<option value="all">All teams</option>
			{#each data.teams as team (team)}
				<option value={team}>{team}</option>
			{/each}
		</select>
		<select bind:value={statusFilter} class="input w-auto text-sm">
			<option value="all">All statuses</option>
			{#each statuses as s (s)}
				<option value={s}>{s}</option>
			{/each}
		</select>
	</div>
</div>

<div class="mt-4 rounded-lg border border-blue-900 bg-blue-950/40 px-4 py-3 text-sm text-blue-100">
	<p>
		<span class="font-medium">Queued</span> jobs are waiting for the shared Mac Studio.
		<span class="font-medium">Running</span> jobs are being processed. You can close this page and
		come back later.
	</p>
	<p class="mt-1 text-blue-200/80">Select a row to see its queue position, log, result, or failure explanation.</p>
</div>

<div class="card mt-4 overflow-x-auto">
	<table class="w-full text-sm">
		<thead>
			<tr class="border-b border-border-subtle text-left text-xs uppercase tracking-wide text-zinc-500">
				<th class="px-4 py-3 font-medium">Job ID</th>
				<th class="px-4 py-3 font-medium">Team</th>
				<th class="px-4 py-3 font-medium">Type</th>
				<th class="px-4 py-3 font-medium">Status</th>
				<th class="px-4 py-3 font-medium">Submitted</th>
				<th class="px-4 py-3 font-medium">Duration</th>
			</tr>
		</thead>
		<tbody>
			{#each filteredJobs as job (job.id)}
				<tr
					class="cursor-pointer border-b border-border-subtle last:border-0 hover:bg-bg-subtle"
					onclick={() => (window.location.href = `/jobs/${job.id}`)}
				>
					<td class="px-4 py-3 font-mono text-xs text-zinc-300">{job.id}</td>
					<td class="px-4 py-3 text-zinc-300">{job.team}</td>
					<td class="px-4 py-3 text-zinc-400">{jobTypeLabel(job.type)}</td>
					<td class="px-4 py-3"><span class={statusBadgeClass(job.status)}>{job.status}</span></td>
					<td class="px-4 py-3 text-zinc-400">{formatTimestamp(job.submittedAt)}</td>
					<td class="px-4 py-3 font-mono text-xs text-zinc-400"
						>{formatDuration(job.startedAt, job.completedAt)}</td
					>
				</tr>
			{:else}
				<tr>
					<td colspan="6" class="px-4 py-10 text-center text-zinc-500">No jobs match these filters.</td>
				</tr>
			{/each}
		</tbody>
	</table>
</div>

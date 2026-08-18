<script lang="ts">
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import type { ActionData, PageData } from './$types';
	import { statusBadgeClass, formatDuration, formatTimestamp } from '$lib/format';

	let { data, form }: { data: PageData; form: ActionData } = $props();

	$effect(() => {
		if (!data.authed) return;
		const interval = setInterval(() => invalidateAll(), 10_000);
		return () => clearInterval(interval);
	});
</script>

<svelte:head>
	<title>Admin — Distill Scheduler</title>
</svelte:head>

{#if !data.authed}
	<div class="mx-auto max-w-sm">
		<h1 class="text-xl font-semibold text-zinc-100">Admin login</h1>
		{#if form?.error}
			<div class="mt-4 rounded-md border border-red-800 bg-red-950 px-4 py-3 text-sm text-red-300">
				{form.error}
			</div>
		{/if}
		<form method="POST" action="?/login" use:enhance class="mt-4 space-y-4">
			<div>
				<label class="label" for="password">Password</label>
				<input id="password" name="password" type="password" class="input" required />
			</div>
			<button type="submit" class="btn-primary w-full">Sign in</button>
		</form>
	</div>
{:else}
	<div class="flex items-center justify-between">
		<h1 class="text-xl font-semibold text-zinc-100">Admin</h1>
		<form method="POST" action="?/logout" use:enhance>
			<button type="submit" class="btn-secondary">Sign out</button>
		</form>
	</div>

	<div class="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
		<div class="card p-4">
			<h2 class="text-xs font-medium uppercase tracking-wide text-zinc-500">Dagu server</h2>
			<div class="mt-2 flex items-center gap-2">
				<span class="h-2 w-2 rounded-full {data.daguHealthy ? 'bg-emerald-500' : 'bg-red-500'}"
				></span>
				<span class="text-sm text-zinc-300">{data.daguHealthy ? 'Healthy' : 'Unreachable'}</span>
			</div>
		</div>
		<div class="card p-4">
			<h2 class="text-xs font-medium uppercase tracking-wide text-zinc-500">Disk usage (~/.distill)</h2>
			<div class="mt-2 font-mono text-sm text-zinc-300">{data.diskUsage}</div>
		</div>
	</div>

	<div class="card mt-6 overflow-x-auto">
		<table class="w-full text-sm">
			<thead>
				<tr
					class="border-b border-border-subtle text-left text-xs uppercase tracking-wide text-zinc-500"
				>
					<th class="px-4 py-3 font-medium">Job ID</th>
					<th class="px-4 py-3 font-medium">Team</th>
					<th class="px-4 py-3 font-medium">Type</th>
					<th class="px-4 py-3 font-medium">Status</th>
					<th class="px-4 py-3 font-medium">Submitted</th>
					<th class="px-4 py-3 font-medium">Duration</th>
					<th class="px-4 py-3 font-medium"></th>
				</tr>
			</thead>
			<tbody>
				{#each data.jobs as job (job.id)}
					<tr class="border-b border-border-subtle last:border-0 hover:bg-bg-subtle">
						<td class="px-4 py-3">
							<a href="/jobs/{job.id}" class="font-mono text-xs text-zinc-300 hover:underline"
								>{job.id}</a
							>
						</td>
						<td class="px-4 py-3 text-zinc-300">{job.team}</td>
						<td class="px-4 py-3 text-zinc-400">{job.type}</td>
						<td class="px-4 py-3"
							><span class={statusBadgeClass(job.status)}>{job.status}</span></td
						>
						<td class="px-4 py-3 text-zinc-400">{formatTimestamp(job.submittedAt)}</td>
						<td class="px-4 py-3 font-mono text-xs text-zinc-400"
							>{formatDuration(job.startedAt, job.completedAt)}</td
						>
						<td class="px-4 py-3 text-right">
							{#if job.status === 'queued' || job.status === 'running'}
								<form method="POST" action="?/cancel" use:enhance>
									<input type="hidden" name="jobId" value={job.id} />
									<button type="submit" class="btn-danger">Cancel</button>
								</form>
							{/if}
						</td>
					</tr>
				{:else}
					<tr>
						<td colspan="7" class="px-4 py-10 text-center text-zinc-500">No jobs yet.</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}

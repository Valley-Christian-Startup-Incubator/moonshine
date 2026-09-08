<script lang="ts">
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import type { ActionData, PageData } from './$types';
	import {
		statusBadgeClass,
		formatDuration,
		formatTimestamp
	} from '$lib/format';
	import InfoDialog from '$lib/components/InfoDialog.svelte';

	let { data, form }: { data: PageData; form: ActionData } = $props();
	let teacherDraft = $state<string | null>(null);
	let savingTeacher = $state(false);
	let studentDrafts = $state<Record<string, string>>({});
	let savingStudent = $state<string | null>(null);

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
		<h1 class="text-xl font-semibold text-zinc-100">Instructor sign in</h1>
		<p class="mt-2 text-sm text-zinc-400">
			Sign in to configure teacher and student models or manage jobs.
		</p>
		{#if form?.error}
			<div
				class="mt-4 rounded-md border border-red-800 bg-red-950 px-4 py-3 text-sm text-red-300"
			>
				{form.error}
			</div>
		{/if}
		<form method="POST" action="?/login" use:enhance class="mt-4 space-y-4">
			<div>
				<label class="label" for="password">Password</label>
				<input
					id="password"
					name="password"
					type="password"
					class="input"
					required
				/>
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

	<section
		id="teacher-model"
		class="card mt-6 scroll-mt-6 p-5"
		aria-labelledby="teacher-heading"
	>
		<div class="flex items-center gap-2">
			<h2 id="teacher-heading" class="text-base font-medium text-zinc-100">
				Configure teacher · {data.teacherSettings.label}
			</h2>
			<InfoDialog title="Local teacher folder"
				><p>
					<strong>1.</strong> Locate the installed Qwen model in MLX format on
					the computer running Moonshine.
				</p>
				<p>
					<strong>2.</strong> Paste its full folder path below. The folder needs config.json,
					tokenizer.json, and its safetensors weights. An Ollama model name or Hugging
					Face ID will not work.
				</p>
				<p>
					<strong>3.</strong> Save the source, then return to the Lab to generate
					teacher answers. This saves a folder reference, not a copy of the weights.
					New jobs use this source.
				</p>
				<p>
					The check verifies the Qwen configuration and required files, not
					inference or the parameter count. Use the instructor-approved
					model.
				</p></InfoDialog
			>
		</div>
		<p class="mt-2 text-sm text-zinc-400">
			Point Moonshine to the existing model. Nothing is downloaded.
		</p>
		<form
			method="POST"
			action="?/saveTeacher"
			class="mt-4"
			use:enhance={() => {
				savingTeacher = true;
				return async ({ update }) => {
					try {
						await update({ reset: false });
					} finally {
						savingTeacher = false;
					}
				};
			}}
		>
			<label for="teacher-path" class="label">Local model folder</label>
			<input
				id="teacher-path"
				name="teacherPath"
				type="text"
				value={teacherDraft ?? data.teacherSettings.path}
				oninput={(event) => (teacherDraft = event.currentTarget.value)}
				class="input font-mono"
				placeholder="/absolute/path/to/qwen-30b"
				required
			/>
			<p class="mt-2 text-xs text-zinc-500">
				Use a folder on the computer running Moonshine.
			</p>
			{#if form?.teacherError}<p role="alert" class="mt-3 text-sm text-red-300">
					{form.teacherError}
				</p>{:else if data.teacherSettings.error}<p
					role="alert"
					class="mt-3 text-sm text-amber-200"
				>
					{data.teacherSettings.error}
				</p>{/if}
			{#if form?.teacherSaved}<p
					role="status"
					class="mt-3 text-sm text-emerald-300"
				>
					Teacher source saved. New jobs will use this folder.
				</p>{/if}
			<div class="mt-4 flex flex-wrap items-center gap-4">
				<button type="submit" class="btn-primary" disabled={savingTeacher}
					>{savingTeacher ? 'Checking…' : 'Check & save teacher'}</button
				><a
					href="/?jobType=teacher-gen"
					class="text-sm text-blue-300 hover:underline"
					>Back to teacher answers →</a
				>
			</div>
		</form>
	</section>


	<section id="student-models" class="card mt-6 scroll-mt-6 p-5" aria-labelledby="student-heading">
		<h2 id="student-heading" class="text-base font-medium text-zinc-100">Configure student models</h2>
		<p class="mt-2 text-sm text-zinc-400">Choose the installed Qwen 4B and 8B MLX folders on the computer running Moonshine. Each folder needs config.json, tokenizer.json, and its safetensors weights. Nothing is downloaded.</p>
		<p class="mt-2 text-xs text-zinc-500">The check verifies required files, not inference or parameter count. Use the instructor-approved model for each size.</p>
		{#each ['qwen-4b', 'qwen-8b'] as id}
			{@const settings = data.studentSettings[id as 'qwen-4b' | 'qwen-8b']}
			{@const label = id === 'qwen-4b' ? 'Qwen 4B' : 'Qwen 8B'}
			<form method="POST" action="?/saveStudent" class="mt-5 border-t border-border-subtle pt-5"
				use:enhance={() => {
					savingStudent = id;
					return async ({ update }) => {
						try { await update({ reset: false }); }
						finally { savingStudent = null; }
					};
				}}>
				<input type="hidden" name="studentId" value={id} />
				<label for={`${id}-path`} class="label">{label} local model folder</label>
				<input id={`${id}-path`} name="studentPath" type="text" class="input font-mono"
					value={studentDrafts[id] ?? (form?.studentId === id ? form?.studentPath : undefined) ?? settings.path}
					oninput={(event) => (studentDrafts[id] = event.currentTarget.value)}
					placeholder={`/absolute/path/to/${id}`} required />
				<p class="mt-2 text-xs text-zinc-400">{settings.error ? 'Needs attention' : settings.source === 'none' ? 'Not configured' : `Configured · ${settings.label}`} · {settings.source === 'saved' ? 'Saved in Moonshine' : settings.source === 'environment' ? 'Server environment' : 'No source saved'}</p>
				{#if form?.studentId === id && form?.studentError}
					<p role="alert" class="mt-3 text-sm text-red-300">{form.studentError}</p>
				{:else if settings.error}
					<p role="alert" class="mt-3 text-sm text-amber-200">{settings.error}</p>
				{/if}
				{#if form?.studentSaved === id}<p role="status" class="mt-3 text-sm text-emerald-300">{label} source saved. New jobs will use this folder.</p>{/if}
				<button type="submit" class="btn-primary mt-4" disabled={savingStudent !== null}>{savingStudent === id ? 'Checking…' : `Check & save ${label}`}</button>
			</form>
		{/each}
		<a href="/?jobType=finetune" class="mt-5 inline-block text-sm text-blue-300 hover:underline">Back to training →</a>
	</section>

	<div class="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
		<div class="card p-4">
			<h2 class="text-xs font-medium uppercase tracking-wide text-zinc-500">
				Dagu server
			</h2>
			<div class="mt-2 flex items-center gap-2">
				<span
					class="h-2 w-2 rounded-full {data.daguHealthy
						? 'bg-emerald-500'
						: 'bg-red-500'}"
				></span>
				<span class="text-sm text-zinc-300"
					>{data.daguHealthy ? 'Healthy' : 'Unreachable'}</span
				>
			</div>
		</div>
		<div class="card p-4">
			<h2 class="text-xs font-medium uppercase tracking-wide text-zinc-500">
				Disk usage (~/.distill)
			</h2>
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
					<tr
						class="border-b border-border-subtle last:border-0 hover:bg-bg-subtle"
					>
						<td class="px-4 py-3">
							<a
								href="/jobs/{job.id}"
								class="font-mono text-xs text-zinc-300 hover:underline"
								>{job.id}</a
							>
						</td>
						<td class="px-4 py-3 text-zinc-300">{job.team}</td>
						<td class="px-4 py-3 text-zinc-400">{job.type}</td>
						<td class="px-4 py-3"
							><span class={statusBadgeClass(job.status)}>{job.status}</span
							></td
						>
						<td class="px-4 py-3 text-zinc-400"
							>{formatTimestamp(job.submittedAt)}</td
						>
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
						<td colspan="7" class="px-4 py-10 text-center text-zinc-500"
							>No jobs yet.</td
						>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}

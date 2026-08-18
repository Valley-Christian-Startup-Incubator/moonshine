<script lang="ts">
	import { enhance } from '$app/forms';
	import type { ActionData, PageData } from './$types';
	import type { JobType } from '$lib/types';

	let { data, form }: { data: PageData; form: ActionData } = $props();

	let selectedTeam = $state(data.teams[0]);
	let selectedType = $state<JobType>(data.jobTypes[0].value);
	let selectedFile = $state<File | null>(null);
	let dragActive = $state(false);
	let submitting = $state(false);

	const activeFields = $derived(data.fields[selectedType]);

	function onDrop(e: DragEvent) {
		e.preventDefault();
		dragActive = false;
		const f = e.dataTransfer?.files?.[0];
		if (f) selectedFile = f;
	}

	function onFileInput(e: Event) {
		const target = e.target as HTMLInputElement;
		selectedFile = target.files?.[0] ?? null;
	}

	function formatBytes(bytes: number): string {
		if (bytes < 1024) return `${bytes} B`;
		if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
		return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
	}
</script>

<svelte:head>
	<title>Submit Job — Distill Scheduler</title>
</svelte:head>

<div class="mx-auto max-w-2xl">
	<h1 class="text-xl font-semibold text-zinc-100">Submit a job</h1>
	<p class="mt-1 text-sm text-zinc-400">
		Jobs run serially on the Mac Studio. Pick a team, a job type, drop your input file, and go.
	</p>

	{#if form?.error}
		<div class="mt-6 rounded-md border border-red-800 bg-red-950 px-4 py-3 text-sm text-red-300">
			{form.error}
		</div>
	{/if}

	<form
		method="POST"
		enctype="multipart/form-data"
		class="mt-6 space-y-6"
		use:enhance={() => {
			submitting = true;
			return async ({ update }) => {
				await update();
				submitting = false;
			};
		}}
	>
		<div>
			<label class="label" for="team">Team</label>
			<select id="team" name="team" bind:value={selectedTeam} class="input">
				{#each data.teams as team (team)}
					<option value={team}>{team}</option>
				{/each}
			</select>
		</div>

		<div>
			<span class="label">Job type</span>
			<div class="grid grid-cols-1 gap-2 sm:grid-cols-2">
				{#each data.jobTypes as jt (jt.value)}
					<button
						type="button"
						onclick={() => (selectedType = jt.value)}
						class="rounded-md border px-3 py-2.5 text-left text-sm transition-colors
							{selectedType === jt.value
							? 'border-zinc-400 bg-bg-elevated text-zinc-100'
							: 'border-border bg-bg-subtle text-zinc-400 hover:border-zinc-600'}"
					>
						<div class="font-medium">{jt.label}</div>
						<div class="mt-0.5 text-xs text-zinc-500">{jt.description}</div>
					</button>
				{/each}
			</div>
			<input type="hidden" name="jobType" value={selectedType} />
		</div>

		<div>
			<span class="label">Input file</span>
			<label
				for="file"
				ondragover={(e) => {
					e.preventDefault();
					dragActive = true;
				}}
				ondragleave={() => (dragActive = false)}
				ondrop={onDrop}
				class="flex cursor-pointer flex-col items-center justify-center rounded-md border-2 border-dashed px-6 py-10 text-center transition-colors
					{dragActive ? 'border-zinc-400 bg-bg-elevated' : 'border-border bg-bg-subtle'}"
			>
				{#if selectedFile}
					<div class="font-mono text-sm text-zinc-200">{selectedFile.name}</div>
					<div class="mt-1 text-xs text-zinc-500">{formatBytes(selectedFile.size)}</div>
				{:else}
					<div class="text-sm text-zinc-400">Drag & drop a file here, or click to browse</div>
					<div class="mt-1 text-xs text-zinc-600">JSONL input · up to 500MB</div>
				{/if}
			</label>
			<input
				id="file"
				name="file"
				type="file"
				class="sr-only"
				required
				onchange={onFileInput}
			/>
		</div>

		{#if activeFields.length > 0}
			<div>
				<span class="label">Parameters</span>
				<div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
					{#each activeFields as field (field.name)}
						<div>
							<label class="mb-1 block text-xs text-zinc-500" for={field.name}
								>{field.label}</label
							>
							<input
								id={field.name}
								name={field.name}
								type={field.type}
								step={field.step}
								value={field.default}
								class="input font-mono text-xs"
							/>
						</div>
					{/each}
				</div>
			</div>
		{/if}

		<button type="submit" class="btn-primary w-full" disabled={submitting}>
			{submitting ? 'Submitting…' : 'Submit job'}
		</button>
	</form>
</div>

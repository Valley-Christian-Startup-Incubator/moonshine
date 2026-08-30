<script lang="ts">
	import { enhance } from '$app/forms';
	import { untrack } from 'svelte';
	import type { ActionData, PageData } from './$types';
	import { GLOSSARY_TERMS, type JobType, type Team } from '$lib/types';

	let { data, form }: { data: PageData; form: ActionData } = $props();

	let selectedTeam = $state<Team>('FRC Robotics');
	let selectedType = $state<JobType>(untrack(() => data.initialType));
	let selectedFile = $state<File | null>(null);
	let dragActive = $state(false);
	let submitting = $state(false);
	let fileInput = $state<HTMLInputElement>();

	const activeFields = $derived(data.fields[selectedType]);
	const activeJobType = $derived(data.jobTypes.find((jobType) => jobType.value === selectedType)!);

	function selectJobType(jobType: JobType) {
		selectedType = jobType;
		selectedFile = null;
		if (fileInput) fileInput.value = '';
	}

	function onDrop(e: DragEvent) {
		e.preventDefault();
		dragActive = false;
		const f = e.dataTransfer?.files?.[0];
		if (f) {
			selectedFile = f;
			if (fileInput && e.dataTransfer) fileInput.files = e.dataTransfer.files;
		}
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
		Choose what you want to do and follow the file instructions below. Jobs run one at a time on
		the shared Mac Studio.
	</p>

	<div class="mt-5 rounded-lg border border-blue-900 bg-blue-950/40 px-4 py-3 text-sm text-blue-100">
		<p class="font-medium">You can leave after submitting.</p>
		<p class="mt-1 text-blue-200/80">
			Open Jobs later to check your place in line, watch progress, and download the result.
		</p>
	</div>

	<section id="guide" class="card mt-5 scroll-mt-6 overflow-hidden" aria-labelledby="usual-workflow">
		<div class="border-b border-border-subtle px-4 py-3">
			<h2 id="usual-workflow" class="text-sm font-medium text-zinc-100">
				The usual three-step workflow
			</h2>
			<p class="mt-1 text-sm text-zinc-400">
				Start with the first step you still need. Most teams finish with Fine-tune a model.
			</p>
		</div>
		<ol class="grid grid-cols-1 gap-px bg-border-subtle sm:grid-cols-3">
			<li class="bg-bg-elevated p-4">
				<button type="button" class="w-full text-left" onclick={() => selectJobType('prompt-gen')}>
					<span class="text-xs font-medium text-blue-300">Step 1, optional</span>
					<span class="mt-1 block text-sm font-medium text-zinc-100">Generate prompts</span>
					<span class="mt-1 block text-xs leading-5 text-zinc-500">
						Skip this if you already have a prompt file.
					</span>
				</button>
			</li>
			<li class="bg-bg-elevated p-4">
				<button type="button" class="w-full text-left" onclick={() => selectJobType('teacher-gen')}>
					<span class="text-xs font-medium text-blue-300">Step 2</span>
					<span class="mt-1 block text-sm font-medium text-zinc-100">Generate teacher answers</span>
					<span class="mt-1 block text-xs leading-5 text-zinc-500">
						Create the examples used for training.
					</span>
				</button>
			</li>
			<li class="bg-bg-elevated p-4">
				<button type="button" class="w-full text-left" onclick={() => selectJobType('finetune')}>
					<span class="text-xs font-medium text-emerald-300">Step 3, recommended</span>
					<span class="mt-1 block text-sm font-medium text-zinc-100">Fine-tune a model</span>
					<span class="mt-1 block text-xs leading-5 text-zinc-500">
						Teach the model from the saved examples.
					</span>
				</button>
			</li>
		</ol>
		<div class="border-t border-border-subtle bg-bg-subtle px-4 py-3 text-xs leading-5 text-zinc-400">
			<button type="button" class="font-medium text-amber-300 hover:underline" onclick={() => selectJobType('distill')}>
				Distillation
			</button>
			is an advanced alternative to step 3.
			<button type="button" class="ml-1 font-medium text-amber-300 hover:underline" onclick={() => selectJobType('quantize')}>
				Make a model smaller
			</button>
			is a separate tool, not part of the training workflow.
		</div>
	</section>

	<details class="card mt-5">
		<summary class="cursor-pointer px-4 py-3 text-sm font-medium text-zinc-200">
			Terms used in Moonshine
		</summary>
		<dl class="grid grid-cols-1 gap-x-6 gap-y-4 border-t border-border-subtle px-4 py-4 sm:grid-cols-2">
			{#each GLOSSARY_TERMS as item (item.term)}
				<div>
					<dt class="text-sm font-medium text-zinc-200">{item.term}</dt>
					<dd class="mt-1 text-xs leading-5 text-zinc-500">{item.definition}</dd>
				</div>
			{/each}
		</dl>
	</details>

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
						onclick={() => selectJobType(jt.value)}
						aria-pressed={selectedType === jt.value}
						class="rounded-md border px-3 py-2.5 text-left text-sm transition-colors
							{selectedType === jt.value
							? 'border-zinc-400 bg-bg-elevated text-zinc-100'
							: 'border-border bg-bg-subtle text-zinc-400 hover:border-zinc-600'}"
					>
						<div class="flex items-center justify-between gap-2">
							<div class="font-medium">{jt.label}</div>
							<span
								class="rounded-full px-2 py-0.5 text-[10px] font-medium
									{jt.badgeTone === 'recommended'
									? 'bg-emerald-950 text-emerald-300'
									: jt.badgeTone === 'advanced'
										? 'bg-amber-950 text-amber-300'
										: 'bg-zinc-800 text-zinc-400'}"
								>{jt.badge}</span
							>
						</div>
						<div class="mt-0.5 text-xs text-zinc-500">{jt.description}</div>
					</button>
				{/each}
			</div>
			<input type="hidden" name="jobType" value={selectedType} />
		</div>

		<div class="card overflow-hidden">
			<div class="border-b border-border-subtle px-4 py-3">
				<p class="text-sm font-medium text-zinc-100">{activeJobType.label}</p>
				<p class="mt-1 text-sm leading-6 text-zinc-400">{activeJobType.description}</p>
			</div>
			<dl class="grid grid-cols-1 gap-px bg-border-subtle sm:grid-cols-2">
				<div class="bg-bg-elevated px-4 py-3">
					<dt class="text-xs font-medium uppercase tracking-wide text-zinc-500">Before you start</dt>
					<dd class="mt-1 text-sm leading-5 text-zinc-300">{activeJobType.prerequisite}</dd>
				</div>
				<div class="bg-bg-elevated px-4 py-3">
					<dt class="text-xs font-medium uppercase tracking-wide text-zinc-500">What you get</dt>
					<dd class="mt-1 text-sm leading-5 text-zinc-300">{activeJobType.output}</dd>
				</div>
				<div class="bg-bg-elevated px-4 py-3">
					<dt class="text-xs font-medium uppercase tracking-wide text-zinc-500">After it finishes</dt>
					<dd class="mt-1 text-sm leading-5 text-zinc-300">{activeJobType.nextStep}</dd>
				</div>
				<div class="bg-bg-elevated px-4 py-3">
					<dt class="text-xs font-medium uppercase tracking-wide text-zinc-500">Typical time</dt>
					<dd class="mt-1 text-sm leading-5 text-zinc-300">{activeJobType.duration}</dd>
				</div>
			</dl>
			{#if activeJobType.warning}
				<p class="border-t border-amber-900/70 bg-amber-950/40 px-4 py-3 text-sm text-amber-200">
					{activeJobType.warning}
				</p>
			{/if}
		</div>

		{#if activeJobType.requiresInput}
			<div>
				<span class="label">{activeJobType.inputLabel}</span>
				<p class="mb-2 text-sm text-zinc-400">{activeJobType.inputHelp}</p>
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
						<div class="mt-1 text-xs text-zinc-600">JSONL file, up to 500MB</div>
					{/if}
				</label>
				<input
					bind:this={fileInput}
					id="file"
					name="file"
					type="file"
					accept=".jsonl"
					class="sr-only"
					required
					onchange={onFileInput}
				/>
				{#if activeJobType.inputExample}
					<div class="mt-2 rounded-md border border-border-subtle bg-black/40 px-3 py-2">
						<div class="flex items-center justify-between gap-3">
							<div class="text-xs text-zinc-500">One line should look like this:</div>
							{#if activeJobType.exampleDownload}
								<a
									href={activeJobType.exampleDownload}
									download
									class="text-xs font-medium text-blue-300 hover:underline"
								>
									Download example file
								</a>
							{/if}
						</div>
						<code class="mt-1 block overflow-x-auto text-xs text-zinc-300"
							>{activeJobType.inputExample}</code
						>
					</div>
				{/if}
				</div>
		{:else}
			<div
				class="rounded-md border border-emerald-900 bg-emerald-950/30 px-4 py-3 text-sm text-emerald-200"
			>
				No upload is needed. Confirm the model under Advanced settings, then submit.
			</div>
		{/if}

		{#if activeFields.length > 0}
			<details class="card group">
				<summary
					class="cursor-pointer list-none px-4 py-3 text-sm font-medium text-zinc-200"
				>
					<span class="flex items-center justify-between gap-3">
						<span>Advanced settings</span>
						<span class="text-xs font-normal text-zinc-500 group-open:hidden"
							>Recommended defaults are selected</span
						>
						<span class="hidden text-xs font-normal text-zinc-500 group-open:inline"
							>Hide settings</span
						>
					</span>
				</summary>
				<div class="border-t border-border-subtle px-4 py-4">
					<p class="mb-4 text-sm text-zinc-400">
						You do not need to understand or change these settings for a normal job. The
						defaults are set for the shared Mac Studio. Change them only when your instructor
						or experiment plan calls for it.
					</p>
					<div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
						{#each activeFields as field (field.name)}
							<div>
								<label class="mb-1 block text-xs font-medium text-zinc-300" for={field.name}
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
								<p class="mt-1.5 text-xs leading-5 text-zinc-500">{field.help}</p>
							</div>
						{/each}
					</div>
				</div>
			</details>
		{/if}

		<button type="submit" class="btn-primary w-full" disabled={submitting}>
			{submitting ? 'Submitting…' : 'Submit job'}
		</button>
	</form>
</div>

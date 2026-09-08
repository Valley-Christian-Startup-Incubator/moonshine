<script lang="ts">
	import JsonExample from '$lib/components/JsonExample.svelte';
	import { enhance } from '$app/forms';
	import { untrack, tick } from 'svelte';
	import type { ActionData, PageData } from './$types';
	import type { JobType, Team } from '$lib/types';
	import DatasetReview from '$lib/components/DatasetReview.svelte';
	import InfoDialog from '$lib/components/InfoDialog.svelte';

	let { data, form }: { data: PageData; form: ActionData } = $props();
	let selectedTeam = $state<Team>('FRC Robotics');
	let selectedType = $state<JobType>(untrack(() => data.initialType));
	let stage = $state(
		untrack(() =>
			['finetune', 'distill', 'quantize'].includes(data.initialType) ? 2 : 0
		)
	);
	let selectedFile = $state<File | null>(null);
	let studentModel = $state('qwen-4b');
	let submitting = $state(false);
	let fileInput = $state<HTMLInputElement>();
	let tour = $state<number | null>(null);
	let tourButton = $state<HTMLButtonElement>();
	const stages = [
		{ title: 'Generate' },
		{ title: 'Split' },
		{ title: 'Train' },
		{ title: 'Evaluate' }
	];
	const tourSteps = [
		{
			target: 'pipeline',
			title: 'One experiment, four stages',
			text: 'Generate examples, set aside validation questions, train, then compare. Click a stage whenever you need it.'
		},
		{
			target: 'task-choice',
			title: 'Start with what you have',
			text: 'Have questions already? Choose Teacher answers and upload them. Otherwise, start from topics.'
		},
		{
			target: 'dataset',
			title: 'Check your examples',
			text: 'Choose a JSONL file to browse samples before submitting. Keep a copy on your computer.'
		},
		{
			target: 'run-job',
			title: 'Run it, then save the result',
			text: 'Submit once. Open Jobs to follow progress and download the output. Next, use Split to freeze a separate validation file.'
		}
	];
	const activeJobType = $derived(
		data.jobTypes.find((job) => job.value === selectedType)!
	);
	const studentJob = $derived(
		['finetune', 'distill', 'quantize'].includes(selectedType)
	);
	const needsTeacher = $derived(
		selectedType === 'teacher-gen' || selectedType === 'distill'
	);
	const advancedFields = $derived(
		data.fields[selectedType].filter(
			(field) =>
				!['MODEL_PATH', 'TEACHER_MODEL_PATH', 'Q_BITS'].includes(field.name) &&
				!(
					selectedType === 'teacher-gen' &&
					['MAX_TOKENS', 'TEMPERATURE'].includes(field.name)
				)
		)
	);
	const modelsReady = $derived(
		(!studentJob ||
			!!data.modelPresets.students.find((model) => model.id === studentModel)
				?.available) &&
			(!needsTeacher || data.modelPresets.teacher.available)
	);

	function chooseType(type: JobType) {
		if (selectedType !== type) {
			selectedFile = null;
			if (fileInput) fileInput.value = '';
		}
		selectedType = type;
	}
	function chooseStage(index: number) {
		stage = index;
		if (index === 0 && studentJob) chooseType('teacher-gen');
		if (index === 2 && !studentJob) chooseType('finetune');
		tour = null;
	}
	async function showTour(index: number) {
		stage = 0;
		if (studentJob) chooseType('teacher-gen');
		tour = index;
		await tick();
		document.getElementById(tourSteps[index].target)?.scrollIntoView({
			block: 'center',
			behavior: matchMedia('(prefers-reduced-motion: reduce)').matches
				? 'instant'
				: 'smooth'
		});
		document.getElementById('tour-next')?.focus({ preventScroll: true });
	}
	function closeTour() {
		tour = null;
		tourButton?.focus();
	}
</script>

<svelte:head><title>Moonshine · Teach a smaller model</title></svelte:head>
<svelte:window
	onkeydown={(event) => {
		if (event.key === 'Escape' && tour !== null) closeTour();
	}}
/>

{#snippet coach(index: number)}
	{#if tour === index}
		<aside class="coach" aria-label="Guided walkthrough">
			<span class="coach-arrow" aria-hidden="true">↑</span>
			<div class="min-w-0 flex-1" aria-live="polite">
				<p class="text-xs text-blue-300">
					Quick tour · {index + 1} of {tourSteps.length}
				</p>
				<p class="mt-1 font-medium text-zinc-100">{tourSteps[index].title}</p>
				<p class="mt-1 text-sm leading-6 text-zinc-300">
					{tourSteps[index].text}
				</p>
				<div class="mt-3 flex flex-wrap items-center gap-3">
					{#if index > 0}<button
							type="button"
							class="text-sm text-blue-200"
							onclick={() => showTour(index - 1)}>Back</button
						>{/if}
					<button
						id="tour-next"
						type="button"
						class="btn-primary"
						onclick={() =>
							index < tourSteps.length - 1 ? showTour(index + 1) : closeTour()}
						>{index === tourSteps.length - 1 ? 'Got it' : 'Next'}</button
					>
					<button
						type="button"
						class="text-sm text-zinc-400"
						onclick={closeTour}>Close tour</button
					>
				</div>
			</div>
		</aside>
	{/if}
{/snippet}

<div class="mx-auto max-w-3xl">
	<div class="flex items-center justify-between gap-4">
		<h1 class="text-xl font-semibold tracking-tight text-zinc-100">
			Model lab
		</h1>
		<button
			bind:this={tourButton}
			type="button"
			class="text-xs text-zinc-500 hover:text-zinc-200"
			onclick={() => showTour(0)}>Quick tour ↗</button
		>
	</div>

	<section id="guide" class="mt-7 scroll-mt-6" aria-label="Experiment stages">
		<div id="pipeline" class:tour-target={tour === 0}>
			<ol class="pipeline">
				{#each stages as item, index}<li>
						<button
							type="button"
							class:active={stage === index}
							aria-current={stage === index ? 'step' : undefined}
							onclick={() => chooseStage(index)}
							><span class="step-number">{index + 1}</span><span
								class="font-medium">{item.title}</span
							></button
						>
					</li>{/each}
			</ol>
		</div>
		{@render coach(0)}
	</section>

	<div class="mt-6" hidden={stage !== 1}>
		<section class="workspace">
			<div class="section-heading">
				<h2>Split your examples</h2>
				<InfoDialog title="Training and validation"
					><p>
						Set aside validation questions before training. Use the same saved
						validation file for every model and bit depth.
					</p>
					<p>
						The split happens in your browser. A fixed seed of 42 makes it
						reproducible, and repeated questions stay together so they cannot
						appear in both groups. Actual percentages may vary slightly.
					</p>
					<p>
						Save both files and the split record before leaving. Train only on
						the training file. The current backend does not enforce separation
						for you.
					</p></InfoDialog
				>
			</div>
			<p class="intro">Set aside questions for testing.</p>
			<DatasetReview split />
		</section>
	</div>

	{#if stage === 3}
		<section class="workspace mt-6">
			<div class="section-heading">
				<h2>Evaluate your model</h2>
				<InfoDialog title="Evaluation"
					><p>
						Compare the base and trained student on the same frozen validation
						questions. Never train on this file.
					</p>
					<p>
						<strong>Top-1 token agreement:</strong> how often student and teacher
						choose the same most likely next token, given the same text. Higher is
						better; tokenizers must match.
					</p>
					<p>
						<strong>Perplexity:</strong> how surprised the student is by the teacher’s
						saved answer tokens. Lower is better; score only answer tokens.
					</p>
					<p>
						Repeat at different bits per weight and compare size, speed, and
						quality. These token metrics are not connected yet. The existing
						evaluation script scores answers instead.
					</p></InfoDialog
				>
			</div>
			<p class="intro">Automatic evaluation is not available yet.</p>
			<button
				type="button"
				class="btn-secondary mt-5"
				onclick={() => chooseStage(1)}>View validation split</button
			>
		</section>
	{/if}
	<form
		hidden={stage === 1 || stage === 3}
		method="POST"
		enctype="multipart/form-data"
		class="workspace mt-6"
		use:enhance={() => {
			submitting = true;
			return async ({ update }) => {
				try {
					await update({ reset: false });
				} finally {
					submitting = false;
				}
			};
		}}
	>
		<input type="hidden" name="jobType" value={selectedType} />
		<div id="task-choice" class:tour-target={tour === 1}>
			<div class="section-heading">
				<h2>{stage === 0 ? 'Create examples' : 'Train your model'}</h2>
				<InfoDialog
					title={stage === 0 ? 'Generating examples' : 'Training methods'}
				>
					{#if stage === 0}<p>
							Start from a topic file to make questions, or upload your own
							questions for the teacher to answer.
						</p>
						<p>
							The current question generator uses templates. Review the samples
							before generating teacher answers. An instructor must configure a
							local teacher model before generating answers.
						</p>
					{:else}<p>
							<strong>Train from answers</strong> teaches the student to predict the
							teacher’s saved text.
						</p>
						<p>
							<strong>Distill token scores</strong> also teaches the student from
							the teacher’s scores for possible next tokens. Both models must use
							the same tokenizer.
						</p>
						<p>
							<strong>Reduce model size</strong> stores weights with fewer bits. Test
							each version on the same validation file to compare quality.
						</p>
						<p>
							Current jobs run and store weights on the Mac Studio.
							Student-machine training and weight transfer are not connected
							yet.
						</p>{/if}
				</InfoDialog>
			</div>
			<div class="mt-5 flex flex-wrap gap-2" aria-label="Choose an action">
				{#each stage === 0 ? [{ type: 'prompt-gen', label: 'Make questions' }, { type: 'teacher-gen', label: 'Teacher answers' }] : [{ type: 'finetune', label: 'Train from answers' }, { type: 'distill', label: 'Distill token scores' }, { type: 'quantize', label: 'Reduce model size' }] as item}
					<button
						type="button"
						class="choice"
						class:chosen={selectedType === item.type}
						aria-pressed={selectedType === item.type}
						onclick={() => chooseType(item.type as JobType)}
						>{item.label}</button
					>
				{/each}
			</div>
			<p class="intro">
				{selectedType === 'prompt-gen'
					? 'Upload topics to make questions.'
					: selectedType === 'teacher-gen'
						? 'Upload questions for the teacher to answer.'
						: selectedType === 'quantize'
							? 'Choose a size for your model.'
							: 'Upload your training split only.'}
			</p>
		</div>
		{@render coach(1)}

		{#if stage === 0 || needsTeacher}
			<section
				class="mt-5 rounded-lg border border-border-subtle p-4"
				aria-label="Teacher configuration"
			>
				<div class="flex flex-wrap items-center justify-between gap-3">
					<div>
						<div class="flex items-center gap-1">
							<span class="text-xs text-zinc-400">Teacher</span><InfoDialog
								title="Teacher setup"
							>
								<p>
									The teacher creates the answers your student learns from.
									The model shown here is the source selected by your instructor.
								</p>
								<p>
									<strong>1. Configure the source.</strong> An instructor opens Configure
									teacher and selects the installed Qwen model folder in MLX format.
									No model is downloaded.
								</p>
								<p>
									<strong>2. Generate answers.</strong> Choose Teacher answers and
									upload your question file. Set a maximum answer length and answer
									variety below.
								</p>
								<p>
									<strong>3. Review and save.</strong> Download the output from Jobs,
									inspect samples, then freeze your training and validation split.
								</p>
							</InfoDialog>
						</div>
						<p class="text-sm font-medium text-zinc-100">
							{data.modelPresets.teacher.label} <span class="ml-2 text-xs font-normal text-zinc-500"
								>{data.modelPresets.teacher.available
									? 'Source configured'
									: 'Setup needed'}</span
							>
						</p>
					</div>
					<a href="/admin#teacher-model" class="btn-secondary text-xs"
						>Configure teacher</a
					>
				</div>
				{#if selectedType === 'teacher-gen'}
					<div
						class="mt-4 grid gap-4 border-t border-border-subtle pt-3 sm:grid-cols-2"
					>
						{#each data.fields['teacher-gen'].filter( (field) => ['MAX_TOKENS', 'TEMPERATURE'].includes(field.name) ) as field}
							<div>
								<div class="flex items-center gap-1">
									<label for={field.name} class="text-xs text-zinc-400"
										>{field.label}</label
									><InfoDialog title={field.label}
										><p>{field.help}</p>
										{#if field.name === 'TEMPERATURE'}<p>
												Start at 0.7. Lower it for more predictable answers;
												raise it for more variety.
											</p>{:else}<p>
												Start at 512 tokens. Increase this if your answers need
												more room.
											</p>{/if}</InfoDialog
									>
								</div>
								<input
									id={field.name}
									name={field.name}
									type="number"
									value={field.default}
									min={field.name === 'MAX_TOKENS' ? 1 : 0}
									step={field.step ?? 1}
									class="input"
								/>
							</div>
						{/each}
					</div>
				{:else if selectedType === 'prompt-gen'}<p
						class="mt-3 text-xs text-zinc-500"
					>
						Used for teacher answers. Making questions uses templates.
					</p>{/if}
			</section>
		{/if}

		{#if studentJob || needsTeacher}
			<div class="mt-5 grid gap-4 sm:grid-cols-2">
				{#if studentJob}<div>
						<label class="label" for="student-model">Student model</label
						><select
							id="student-model"
							name="studentModel"
							bind:value={studentModel}
							class="input"
							>{#each data.modelPresets.students as model}<option
									value={model.id}
									>{model.label}{model.available
										? ''
										: ' · unavailable'}</option
								>{/each}</select
						>
					</div>{/if}
				{#if selectedType === 'quantize'}<div>
						<label class="label" for="Q_BITS">Bits per weight</label><select
							id="Q_BITS"
							name="Q_BITS"
							class="input"
							><option value="4">4 bits · smaller</option><option value="8"
								>8 bits · higher precision</option
							></select
						>
					</div>{/if}
			</div>
		{/if}
		{#if activeJobType.requiresInput}
			<div id="dataset" class="mt-6" class:tour-target={tour === 2}>
				<label class="label" for="file">{activeJobType.inputLabel}</label>
				<div id="file-help" class="mb-3 space-y-2 text-sm text-zinc-400">
					{#if selectedType === 'teacher-gen'}
						<p>Give the teacher the questions or instructions you want it to answer.
							Save a plain-text file ending in <code>.jsonl</code>, with one JSON
							object per line. Each object needs a <code>"prompt"</code> field:</p>
						<JsonExample code={'{"prompt":"Explain how a gear ratio changes torque."}\n{"prompt":"List three ways a school can reduce plastic waste."}'} />
						<p>Use double quotes, with no commas between lines and no surrounding
							square brackets. Include only questions; the teacher will add the answers.</p>
						<p>Start with the example below and replace its questions, or upload the
							file downloaded from <strong>Make questions</strong>.</p>
					{:else if selectedType === 'prompt-gen'}
						<p>Save a plain-text <code>.jsonl</code> file with one topic per line.
							Each JSON object has a <code>"topic"</code> string and an
							<code>"n"</code> number: how many questions to make for that topic.</p>
						<JsonExample code={'{"topic":"robotics","n":5}\n{"topic":"plastic waste","n":3}'} />
						<p>Use double quotes around text and a positive whole number for
							<code>"n"</code>. Do not put commas between lines or wrap them in square
							brackets. Download the example below to get started.</p>
					{:else}
						<p>Upload the training file downloaded from <strong>Split</strong>,
							not the full teacher output or the validation file. Keep validation
							examples separate for evaluation.</p>
						<p>The plain-text <code>.jsonl</code> file needs one JSON object per
							line, with nonempty <code>"prompt"</code> (question) and
							<code>"completion"</code> (teacher answer) strings:</p>
						<JsonExample code={activeJobType.inputExample ?? ''} />
						<p>Use double quotes, with no commas between lines or surrounding square
							brackets. Write line breaks inside an answer as <code>\n</code> so each
							example stays on one line. The download below shows the format.</p>
					{/if}
				</div>
				<input
					bind:this={fileInput}
					id="file"
					aria-describedby="file-help"
					name="file"
					type="file"
					accept=".jsonl"
					required
					class="file-control"
					onchange={(event) =>
						(selectedFile = event.currentTarget.files?.[0] ?? null)}
				/>
				<div
					class="mt-2 flex flex-wrap justify-between gap-2 text-xs text-zinc-500"
				>
					<span>JSONL · max 500 MB</span>{#if activeJobType.exampleDownload}<a
							href={activeJobType.exampleDownload}
							download
							class="text-blue-300 hover:underline">Download example</a
						>{/if}
				</div>
				<DatasetReview file={selectedFile} />
			</div>
			{@render coach(2)}
		{/if}

		{#if advancedFields.length}
			<details class="mt-5 border-t border-border-subtle pt-4">
				<summary class="cursor-pointer text-sm text-zinc-400"
					>More options</summary
				>
				<div class="mt-4 grid gap-4 sm:grid-cols-2">
					{#each advancedFields as field (selectedType + field.name)}<div>
							<div class="flex items-center gap-1">
								<label
									class="text-xs font-medium text-zinc-400"
									for={field.name}>{field.label}</label
								><InfoDialog title={field.label}><p>{field.help}</p></InfoDialog
								>
							</div>
							<input
								id={field.name}
								name={field.name}
								type={field.type}
								step={field.step}
								value={field.default}
								class="input"
							/>
						</div>{/each}
				</div>
			</details>
		{/if}
		<div
			id="run-job"
			class="mt-6 border-t border-border-subtle pt-5"
			class:tour-target={tour === 3}
		>
			{#if form?.error}<p
					role="alert"
					class="mb-4 rounded-md border border-red-900 bg-red-950 p-3 text-sm text-red-200"
				>
					{form.error}
				</p>{/if}
			{#if !modelsReady}<div
					class="mb-3 flex items-center gap-1 text-xs text-amber-200"
				>
					Model setup needed<InfoDialog title="Model setup"
						><p>
							An instructor needs to configure a local Qwen model in MLX format
							before this job can run. An Ollama installation alone does not
							configure the trainer.
						</p>
						<p>
							Moonshine does not download a replacement model from a hub.
						</p></InfoDialog
					>
				</div>{/if}
			<div class="flex flex-col items-stretch gap-3 sm:flex-row sm:items-end">
				<div class="flex-1">
					<label class="label" for="team">Your team</label><select
						id="team"
						name="team"
						bind:value={selectedTeam}
						class="input"
						>{#each data.teams as team}<option value={team}>{team}</option
							>{/each}</select
					>
				</div>
				<button
					type="submit"
					class="btn-primary min-h-10"
					disabled={submitting || !modelsReady}
					>{submitting
						? 'Submitting…'
						: selectedType === 'prompt-gen'
							? 'Generate questions →'
							: selectedType === 'teacher-gen'
								? 'Generate answers →'
								: selectedType === 'quantize'
									? 'Reduce model size →'
									: 'Start training →'}</button
				>
			</div>
			<div class="mt-3 flex items-center gap-1 text-xs text-zinc-500">
				Runs on the Studio<InfoDialog title="Job execution"
					><p>
						Jobs run one at a time on the shared Mac Studio. Training and
						quantization save weights there; student-machine execution is not
						connected yet.
					</p>
					<p>
						You can close this page after submitting. Open Jobs to check
						progress and download the result.
					</p></InfoDialog
				>
			</div>
		</div>
		{@render coach(3)}
	</form>
</div>

<style>
	.pipeline {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		gap: 4px;
		padding: 5px;
		border: 1px solid #27272a;
		border-radius: 12px;
		background: #101012;
	}
	.pipeline button {
		display: flex;
		align-items: center;
		gap: 10px;
		width: 100%;
		padding: 10px;
		text-align: left;
		border-radius: 8px;
		color: #a1a1aa;
		font-size: 14px;
	}
	.pipeline button:hover {
		background: #1c1c20;
	}
	.pipeline button.active {
		background: #202a3c;
		color: #dbeafe;
	}
	.step-number {
		display: grid;
		place-items: center;
		width: 25px;
		height: 25px;
		flex-shrink: 0;
		border: 1px solid #3f3f46;
		border-radius: 50%;
		font-size: 12px;
	}
	.active .step-number {
		background: #93c5fd;
		border-color: #93c5fd;
		color: #172033;
	}
	.workspace {
		border: 1px solid #27272a;
		border-radius: 14px;
		background: #141416;
		padding: 28px;
	}
	.section-heading {
		display: flex;
		align-items: center;
		gap: 8px;
	}
	h2 {
		margin-top: 0;
		color: #f4f4f5;
		font-size: 18px;
		font-weight: 600;
		letter-spacing: -0.025em;
	}
	.intro {
		margin-top: 12px;
		color: #a1a1aa;
		font-size: 14px;
		line-height: 1.7;
	}
	.choice {
		border: 1px solid #3f3f46;
		border-radius: 7px;
		padding: 8px 12px;
		color: #a1a1aa;
		font-size: 13px;
	}
	.choice:hover,
	.choice.chosen {
		background: #27272a;
		color: #f4f4f5;
		border-color: #71717a;
	}
	.file-control {
		width: 100%;
		border: 1px dashed #52525b;
		border-radius: 8px;
		background: #18181b;
		padding: 14px 12px;
		color: #a1a1aa;
		font-size: 13px;
	}
	.file-control::file-selector-button {
		border: 1px solid #52525b;
		border-radius: 6px;
		background: #27272a;
		color: #e4e4e7;
		padding: 7px 10px;
		margin-right: 12px;
		cursor: pointer;
	}
	.tour-target {
		outline: 2px solid #93c5fd;
		outline-offset: 7px;
		border-radius: 8px;
	}
	.coach {
		position: relative;
		display: flex;
		gap: 12px;
		margin-top: 18px;
		padding: 18px;
		border: 1px solid #3b82f6;
		border-radius: 10px;
		background: #14233c;
	}
	.coach-arrow {
		color: #93c5fd;
		font-size: 28px;
		animation: point 1s ease-in-out 4;
	}
	@keyframes point {
		0%,
		100% {
			transform: translateY(3px);
		}
		50% {
			transform: translateY(-4px);
		}
	}
	:global(button:focus-visible),
	:global(summary:focus-visible),
	:global(a:focus-visible),
	.file-control:focus-visible {
		outline: 2px solid #93c5fd;
		outline-offset: 3px;
	}
	@media (prefers-reduced-motion: reduce) {
		.coach-arrow {
			animation: none;
		}
	}
	@media (max-width: 540px) {
		.workspace {
			padding: 20px 16px;
		}
		.pipeline button {
			flex-direction: column;
			gap: 7px;
			padding: 10px 3px;
			text-align: center;
			font-size: 12px;
		}
	}
</style>

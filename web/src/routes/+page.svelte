<script lang="ts">
	import { enhance } from '$app/forms';
	import { page } from '$app/stores';
	import { replaceState } from '$app/navigation';
	import { onMount, untrack } from 'svelte';
	import InterviewChat from '$lib/components/InterviewChat.svelte';
	import { formatEstimate } from '$lib/format';
	import { SECONDS_PER_GENERATED_ITEM } from '$lib/types';
	import type { ActionData, PageData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();

	type Stage = 'prompt' | 'questions' | 'train';
	const STAGES: { id: Stage; n: number; label: string }[] = [
		{ id: 'prompt', n: 1, label: 'Generate Prompt' },
		{ id: 'questions', n: 2, label: 'Generate Questions' },
		{ id: 'train', n: 3, label: 'Train' }
	];
	const initial = $page.url.searchParams.get('stage');
	let stage = $state<Stage>(
		untrack(() => (form?.stage as Stage) ?? (initial === 'questions' || initial === 'train' ? initial : 'prompt'))
	);
	function go(s: Stage) {
		stage = s;
		const u = new URL($page.url);
		u.searchParams.set('stage', s);
		replaceState(u, {});
	}

	// ---- PROMPT made in this session
	const PROMPT_STORE = 'moonshine.prompt.v1';
	let sessionPrompt = $state('');
	onMount(() => {
		sessionPrompt = sessionStorage.getItem(PROMPT_STORE) ?? '';
		if (sessionPrompt) source = 'session';
	});
	function onPrompt(p: string) {
		sessionPrompt = p;
		source = 'session';
		sessionStorage.setItem(PROMPT_STORE, p);
	}
	let copied = $state(false);
	async function copyPrompt() {
		await navigator.clipboard.writeText(sessionPrompt);
		copied = true;
		setTimeout(() => (copied = false), 1500);
	}

	// ---- Generate Questions
	let source = $state<'session' | 'file'>('file');
	let count = $state(untrack(() => data.defaultCount));
	const estimate = $derived(formatEstimate((Number(count) || 0) * SECONDS_PER_GENERATED_ITEM));
	let submitting = $state(false);

	// ---- Train
	const firstAvailable = untrack(() => data.students.find((s) => s.available)?.id ?? '');
	let student = $state(firstAvailable);
	let pairInfo = $state<{ valid: number; invalid: number } | null>(null);
	let readingFile = $state(false);
	async function inspectFile(e: Event) {
		const f = (e.currentTarget as HTMLInputElement).files?.[0];
		pairInfo = null;
		if (!f) return;
		readingFile = true;
		const text = await f.text();
		let valid = 0;
		let invalid = 0;
		for (const line of text.split('\n')) {
			const s = line.trim();
			if (!s) continue;
			try {
				const o = JSON.parse(s);
				if (o && typeof o.prompt === 'string' && typeof o.completion === 'string' && o.prompt.trim() && o.completion.trim()) valid++;
				else invalid++;
			} catch {
				invalid++;
			}
		}
		pairInfo = { valid, invalid };
		readingFile = false;
	}
	const slug = $derived(
		($page.data.userName ?? 'you').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 24) || 'user'
	);
	const today = new Date();
	const ymd = `${today.getFullYear()}${String(today.getMonth() + 1).padStart(2, '0')}${String(today.getDate()).padStart(2, '0')}`;

	const submitEnhance = () => {
		submitting = true;
		return async ({ update }: { update: () => Promise<void> }) => {
			await update();
			submitting = false;
		};
	};
</script>

<svelte:head><title>Lab — Moonshine</title></svelte:head>

<h1 class="text-2xl font-semibold text-zinc-100">Lab</h1>
<p class="mt-1 text-sm text-zinc-400">Three steps: write a PROMPT, generate questions from it, then train a small model on the questions.</p>

<div class="mt-5 flex flex-wrap gap-2" role="tablist">
	{#each STAGES as s (s.id)}
		<button type="button" role="tab" aria-selected={stage === s.id} class="tab {stage === s.id ? 'tab-active' : ''}" onclick={() => go(s.id)}>
			<span class="mr-1 text-zinc-500">{s.n}.</span>{s.label}
		</button>
	{/each}
</div>

{#if form?.error}
	<p role="alert" class="mt-4 rounded-md bg-red-950 px-4 py-3 text-sm text-red-300">{form.error}</p>
{/if}

{#if stage === 'prompt'}
	<section class="mt-6 space-y-5">
		<div class="rounded-lg border border-emerald-900 bg-emerald-950/30 px-4 py-3 text-sm text-emerald-100">
			<strong>The result of this step is a PROMPT, not questions.</strong> A PROMPT is a set of instructions that
			tells the teacher model how to write your practice questions. In step 2 you give it to Moonshine, and Moonshine
			writes the questions.
		</div>

		<div class="grid gap-5 lg:grid-cols-3">
			<div class="lg:col-span-2">
				<h2 class="mb-2 text-sm font-semibold text-zinc-200">Path 1: Interview with the teacher (recommended)</h2>
				<p class="mb-3 text-sm text-zinc-400">
					Answer a few questions about your program. When the teacher has enough, it writes your PROMPT.
				</p>
				<InterviewChat teacher={data.teacher} {onPrompt} />
			</div>
			<div>
				<h2 class="mb-2 text-sm font-semibold text-zinc-200">Path 2: Use your own AI</h2>
				<div class="card space-y-3 p-4 text-sm text-zinc-400">
					<p>
						Download the interview instructions (the "meta-prompt") and paste them into ChatGPT, Claude or Gemini. They will
						interview you and write the same kind of PROMPT.
					</p>
					<div class="flex gap-2">
						<a class="btn-secondary" href="/api/meta-prompt?format=md" download>Download .md</a>
						<a class="btn-secondary" href="/api/meta-prompt?format=txt" download>Download .txt</a>
					</div>
					<p class="text-xs text-zinc-500">Save the PROMPT it gives you as a .md or .txt file, then go to step 2.</p>
				</div>
			</div>
		</div>

		{#if sessionPrompt}
			<div class="rounded-lg border-2 border-emerald-700 bg-bg-elevated">
				<div class="flex flex-wrap items-center justify-between gap-2 border-b border-emerald-900 px-4 py-3">
					<div>
						<p class="text-xs font-semibold uppercase tracking-widest text-emerald-400">Your result</p>
						<h2 class="text-lg font-semibold text-zinc-100">PROMPT</h2>
					</div>
					<div class="flex flex-wrap gap-2">
						<form method="POST" action="/api/prompt-download">
							<input type="hidden" name="prompt" value={sessionPrompt} />
							<button class="btn-secondary" name="format" value="md">Download .md</button>
							<button class="btn-secondary" name="format" value="txt">Download .txt</button>
						</form>
						<button type="button" class="btn-secondary" onclick={copyPrompt}>{copied ? 'Copied' : 'Copy'}</button>
						<button type="button" class="btn-primary" onclick={() => go('questions')}>Use it in step 2 →</button>
					</div>
				</div>
				<pre class="max-h-[420px] overflow-auto whitespace-pre-wrap px-4 py-3 font-mono text-xs leading-5 text-zinc-300">{sessionPrompt}</pre>
			</div>
		{/if}
	</section>
{:else if stage === 'questions'}
	<section class="mt-6 max-w-2xl">
		<p class="text-sm text-zinc-400">
			The teacher model (<span class="font-mono text-zinc-300">{data.teacher}</span>) follows your PROMPT and writes
			practice questions with worked answers, one per line in a .jsonl file.
		</p>
		<form method="POST" action="?/generate" enctype="multipart/form-data" class="card mt-4 space-y-5 p-5" use:enhance={submitEnhance}>
			<fieldset class="space-y-2">
				<legend class="label">Your PROMPT</legend>
				{#if sessionPrompt}
					<label class="flex items-center gap-2 text-sm text-zinc-300">
						<input type="radio" name="source" value="session" bind:group={source} /> Use the PROMPT from this session
						<span class="text-xs text-zinc-500">({sessionPrompt.length.toLocaleString()} characters)</span>
					</label>
				{/if}
				<label class="flex items-center gap-2 text-sm text-zinc-300">
					<input type="radio" name="source" value="file" bind:group={source} /> Upload a PROMPT file (.md or .txt)
				</label>
				{#if source === 'file'}
					<input class="input" type="file" name="file" accept=".md,.txt,text/plain,text/markdown" required />
				{:else}
					<input type="hidden" name="promptText" value={sessionPrompt} />
				{/if}
			</fieldset>
			<div>
				<label class="label" for="count">How many questions?</label>
				<input id="count" class="input w-40" type="number" name="count" min="1" max={data.maxCount} bind:value={count} required />
				<p class="mt-1.5 text-xs text-zinc-500">
					Estimated time: {estimate} (about 2–2.5 seconds per question). You can close this page; the job runs in
					the queue and you can pause it.
				</p>
			</div>
			<button class="btn-primary" type="submit" disabled={submitting}>{submitting ? 'Submitting…' : 'Submit'}</button>
		</form>
	</section>
{:else}
	<section class="mt-6 max-w-2xl">
		<p class="text-sm text-zinc-400">
			Moonshine sets aside 20% of your questions for testing, trains the student on the rest, compares the student's
			answers before and after training, and then creates a new Ollama model named after you.
		</p>
		<form method="POST" action="?/train" enctype="multipart/form-data" class="card mt-4 space-y-5 p-5" use:enhance={submitEnhance}>
			<div>
				<label class="label" for="student">Student model</label>
				<select id="student" name="student" class="input" bind:value={student} required>
					{#each data.students as s (s.id)}
						<option value={s.id} disabled={!s.available}>{s.label}{s.available ? '' : ' (not installed)'}</option>
					{/each}
				</select>
				<p class="mt-1.5 text-xs text-zinc-500">Smaller models train faster; bigger models usually learn more.</p>
			</div>
			<div>
				<label class="label" for="qfile">Questions file (.jsonl)</label>
				<input id="qfile" class="input" type="file" name="file" accept=".jsonl" required onchange={inspectFile} />
				{#if readingFile}
					<p class="mt-1.5 text-xs text-zinc-500">Checking the file…</p>
				{:else if pairInfo}
					<p class="mt-1.5 text-xs {pairInfo.valid >= 10 ? 'text-emerald-400' : 'text-red-400'}">
						{pairInfo.valid.toLocaleString()} valid question/answer pairs{pairInfo.invalid ? `, ${pairInfo.invalid} lines skipped` : ''}.
						{#if pairInfo.valid >= 10}
							About {Math.round(pairInfo.valid * 0.8).toLocaleString()} for training and {Math.round(pairInfo.valid * 0.2).toLocaleString()} held back for testing.
						{:else}
							You need at least 10.
						{/if}
					</p>
				{/if}
			</div>
			<p class="text-xs text-zinc-500">
				New model name: <span class="font-mono text-zinc-300">{student}-{slug}-{ymd}</span>
			</p>
			<details class="rounded-md border border-border-subtle px-3 py-2">
				<summary class="cursor-pointer text-sm text-zinc-300">Advanced (leave blank for automatic)</summary>
				<div class="mt-3 grid grid-cols-2 gap-3">
					<label class="text-xs text-zinc-400">Training steps<input class="input mt-1" type="number" name="ITERS" min="1" placeholder="auto (2 passes)" /></label>
					<label class="text-xs text-zinc-400">Passes over the data (epochs)<input class="input mt-1" type="number" name="EPOCHS" min="0.1" step="0.1" placeholder="2" /></label>
					<label class="text-xs text-zinc-400">Learning rate<input class="input mt-1" type="number" name="LEARNING_RATE" min="0" step="any" placeholder="0.0001" /></label>
					<label class="text-xs text-zinc-400">Examples per step (batch size)<input class="input mt-1" type="number" name="BATCH_SIZE" min="1" placeholder="4" /></label>
					<label class="text-xs text-zinc-400">Layers to train<input class="input mt-1" type="number" name="NUM_LAYERS" min="1" placeholder="16" /></label>
					<label class="text-xs text-zinc-400">Max tokens per example<input class="input mt-1" type="number" name="MAX_SEQ_LENGTH" min="128" placeholder="2048" /></label>
				</div>
			</details>
			<button class="btn-primary" type="submit" disabled={submitting || !student || (pairInfo !== null && pairInfo.valid < 10)}>
				{submitting ? 'Submitting…' : 'Train'}
			</button>
		</form>
	</section>
{/if}

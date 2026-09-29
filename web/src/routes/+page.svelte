<script lang="ts">
	import { enhance } from '$app/forms';
	import { page } from '$app/stores';
	import { afterNavigate, replaceState } from '$app/navigation';
	import { onMount, untrack } from 'svelte';
	import InterviewChat from '$lib/components/InterviewChat.svelte';
	import HowMoonshineWorks from '$lib/components/HowMoonshineWorks.svelte';
	import { formatEstimateRange } from '$lib/format';
	import {
		ANSWER_CHECK_OVERHEAD,
		DEFAULT_DOC_ROLE,
		DOC_ROLE_LABELS,
		DOC_ROLES,
		SECONDS_PER_GENERATED_ITEM,
		SECONDS_PER_GROUNDED_ITEM
	} from '$lib/types';
	import type { ActionData, PageData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();

	// The step is chosen with the "How Moonshine works" panel (it is the step switcher) and ?stage=.
	type Stage = 'prompt' | 'questions' | 'train';
	const stageFrom = (v: string | null): Stage => (v === 'questions' || v === 'train' ? v : 'prompt');
	const initial = $page.url.searchParams.get('stage');
	let stage = $state<Stage>(untrack(() => (form?.stage as Stage) ?? stageFrom(initial)));
	// a real navigation to this page (e.g. the header "Lab" link or /?stage=train) re-reads ?stage=
	afterNavigate((nav) => {
		if (nav.type !== 'enter' && nav.to?.url.pathname === '/') stage = stageFrom(nav.to.url.searchParams.get('stage'));
	});
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
	let submitting = $state(false);
	let docInfo = $state<{ n: number; mb: number; bad: string[] } | null>(null);
	let docNames = $state<string[]>([]);
	function inspectDocs(e: Event) {
		const files = Array.from((e.currentTarget as HTMLInputElement).files ?? []);
		docNames = files.map((f) => f.name);
		docInfo = files.length
			? {
					n: files.length,
					mb: files.reduce((a, f) => a + f.size, 0) / (1024 * 1024),
					bad: files.filter((f) => !/\.(pdf|txt|md|markdown)$/i.test(f.name)).map((f) => f.name)
				}
			: null;
	}

	const perItem = $derived(docInfo && !docInfo.bad.length ? SECONDS_PER_GROUNDED_ITEM : SECONDS_PER_GENERATED_ITEM);
	const perItemLo = $derived(perItem * ANSWER_CHECK_OVERHEAD[0]);
	const perItemHi = $derived(perItem * ANSWER_CHECK_OVERHEAD[1]);
	const estimate = $derived(formatEstimateRange((Number(count) || 0) * perItemLo, (Number(count) || 0) * perItemHi));

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

	// ---- Trainer (advanced) settings. Defaults mirror scripts/train_eval.py train_config().
	const TRAINER_FIELDS: { name: string; label: string; min: string; step?: string; placeholder: string; help: string }[] = [
		{
			name: 'ITERS',
			label: 'Training Steps',
			min: '1',
			placeholder: 'auto (from Passes)',
			help: 'A step is one small practice round: the Student looks at a few examples, and the Trainer nudges it toward the right answers. More steps means more practice but a longer job, and too many can make the model memorize instead of learn. Default: blank (automatic), which works out the steps from Passes over the Data and Examples per Step (at least 20, at most 3,000). If you type a number here, it overrides Passes over the Data. Either way, Moonshine saves a checkpoint about every tenth of the steps and keeps the one that scored best on held-out questions, so too many steps costs time but does not ruin the model.'
		},
		{
			name: 'EPOCHS',
			label: 'Passes over the Data',
			min: '0.1',
			step: '0.1',
			placeholder: 'auto: 2, or 1 for 1,000+',
			help: 'How many times the Student reads through all of your training examples. Moonshine turns this into steps: passes × training examples ÷ examples per step. Default: 2, or 1 when there are more than 1,000 training examples (big sets learn enough in one pass, and a second pass mostly memorizes). Try 3 if the validation loss was still falling at the end; this box is ignored if you fill in Training Steps.'
		},
		{
			name: 'LEARNING_RATE',
			label: 'Learning Rate',
			min: '0',
			step: 'any',
			placeholder: '0.0001',
			help: 'How big a change the Trainer makes to the Student after each step. Higher learns faster but can overshoot and make answers worse; lower is safer but slower. Default: 0.0001 (1e-4). If results get worse, try 0.00005; if nothing changes, try 0.0002.'
		},
		{
			name: 'BATCH_SIZE',
			label: 'Examples per Step (Batch Size)',
			min: '1',
			placeholder: '4',
			help: 'How many examples the Student studies together in one step. Bigger batches give steadier learning but use more memory and, when steps are automatic, mean fewer steps for the same number of passes. Default: 4 (Moonshine lowers it automatically if you have very few examples). Most people should leave it at 4.'
		},
		{
			name: 'NUM_LAYERS',
			label: 'Layers to Train',
			min: '1',
			placeholder: '16',
			help: 'An AI model is a stack of layers. The Trainer only adjusts small add-ons (LoRA adapters) on the top layers and leaves the rest frozen. More layers can learn more but is slower and uses more memory; fewer is faster but learns less. Default: 16.'
		},
		{
			name: 'MAX_SEQ_LENGTH',
			label: 'Max Tokens per Example',
			min: '128',
			placeholder: '2048',
			help: 'The longest an example (question plus answer) can be, measured in tokens (a token is roughly 3/4 of a word). Anything longer gets cut off. Raising it lets long answers fit but uses more memory; lowering it speeds things up but may chop answers. Default: 2048 (about 1,500 words), which fits most examples.'
		}
	];

	const submitEnhance = () => {
		submitting = true;
		return async ({ update }: { update: () => Promise<void> }) => {
			await update();
			submitting = false;
		};
	};
</script>

<svelte:head><title>Lab — Moonshine</title></svelte:head>

<HowMoonshineWorks current={stage} onSelect={go} />

{#if form?.error}
	<p role="alert" class="mt-4 rounded-md bg-red-950 px-4 py-3 text-sm text-red-300">{form.error}</p>
{/if}

{#if stage === 'prompt'}
	<section class="mt-2 space-y-5">
		<div class="space-y-3 text-sm text-zinc-300">
			<h2 class="text-lg font-semibold text-zinc-100">This step: Generate Prompt</h2>
			<p>
				In this step an AI asks you questions about your subject, your audience, and what a good answer looks like.
				Then it writes a curated prompt for you.
			</p>
			<div class="rounded-lg border border-emerald-900 bg-emerald-950/30 px-4 py-3 text-emerald-100">
				<strong>The result of this step is a PROMPT, not questions.</strong> A prompt is a set of instructions that tells
				the Teacher how to write your training questions. You'll upload it in step 2, and the Teacher will turn it into
				thousands of training examples. Each one looks like
				<code class="rounded bg-black/30 px-1 font-mono text-xs">{'{"prompt": "...", "completion": "..."}'}</code>.
			</div>
			<p>
				<strong class="text-zinc-100">Choose your path.</strong> Both paths end with the same kind of prompt, so pick
				whichever you prefer.
			</p>
		</div>

		<div class="grid gap-5 lg:grid-cols-3">
			<div class="card border-sky-900 p-4 lg:col-span-2" id="path-local">
				<p class="text-xs font-semibold uppercase tracking-widest text-sky-400">Path 1</p>
				<h3 class="mb-2 text-base font-semibold text-zinc-100">Local (here in Moonshine)</h3>
				<p class="mb-3 text-sm text-zinc-400">
					Moonshine's own Teacher AI interviews you right on this page. Nothing leaves the school server. When you're
					done, download or save the prompt.
				</p>
				<InterviewChat teacher={data.teacher} {onPrompt} />
			</div>
			<div class="card space-y-3 border-violet-900 p-4 text-sm text-zinc-400" id="path-remote">
				<div>
					<p class="text-xs font-semibold uppercase tracking-widest text-violet-400">Path 2</p>
					<h3 class="text-base font-semibold text-zinc-100">Remote (your favorite AI)</h3>
				</div>
				<p>
					Download the interview instructions and paste them into ChatGPT, Claude, Gemini, or another AI. That AI
					interviews you and writes the prompt. Save its final prompt as a file, then bring it back here for step 2.
				</p>
				<div class="flex flex-wrap gap-2">
					<a class="btn-secondary" href="/api/meta-prompt?format=md" download>Download .md</a>
					<a class="btn-secondary" href="/api/meta-prompt?format=txt" download>Download .txt</a>
				</div>
				<p class="text-xs text-zinc-500">Save the prompt it gives you as a .md or .txt file, then go to step 2.</p>
			</div>
		</div>

		<p class="text-sm text-zinc-300">
			Either way, you should leave this step holding one thing: <strong class="text-zinc-100">your prompt file.</strong>
		</p>

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
	<section class="mt-2 max-w-2xl">
		<h2 class="text-lg font-semibold text-zinc-100">This step: Generate Questions</h2>
		<p class="mt-2 text-sm text-zinc-400">
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
				<label class="label" for="docs">Source documents <span class="font-normal text-zinc-500">(optional)</span></label>
				<input
					id="docs"
					class="input"
					type="file"
					name="docs"
					multiple
					accept=".pdf,.txt,.md,application/pdf,text/plain,text/markdown"
					onchange={inspectDocs}
					aria-describedby="docs-help"
				/>
				<p id="docs-help" class="mt-1.5 text-xs leading-5 text-zinc-500">
					PDF, .txt or .md files, such as a game manual, rules, or official NASA documents. The Teacher reads a
					section of these while writing each batch of questions, so the questions are based on the real facts. The
					Student never sees the documents: it has to learn the facts from the questions. Leave empty to generate
					from your PROMPT alone.
				</p>
				{#if docNames.length && !docInfo?.bad.length}
					<fieldset class="mt-3 rounded-md border border-border-subtle px-3 py-2" id="doc-roles">
						<legend class="px-1 text-xs font-medium text-zinc-300">How much should the Teacher use each document?</legend>
						<ul class="space-y-2">
							{#each docNames as name, i (i)}
								<li class="flex flex-wrap items-center justify-between gap-2">
									<span class="min-w-0 truncate font-mono text-xs text-zinc-200" title={name}>{name}</span>
									<select name="docRole" class="input w-auto py-1 text-xs" aria-label="How much to use {name}">
										{#each DOC_ROLES as r (r)}
											<option value={r} selected={r === DEFAULT_DOC_ROLE}>{DOC_ROLE_LABELS[r]}</option>
										{/each}
									</select>
								</li>
							{/each}
						</ul>
						<p class="mt-2 text-xs leading-5 text-zinc-500">
							The Teacher writes each batch of questions from one part of your documents. <strong class="text-zinc-300">Main</strong>
							documents are used about 3 times as often as Supporting ones; use Main for the official document your model is
							about. <strong class="text-zinc-300">Background</strong> documents are used only a little, and only for facts related
							to your topic (good for a general school profile or handbook). If you are not sure, leave everything on Supporting.
							After the job runs, its page shows how many questions came from each document.
						</p>
					</fieldset>
				{/if}
				{#if docInfo}
					<p class="mt-1 text-xs {docInfo.bad.length ? 'text-red-400' : 'text-emerald-400'}">
						{#if docInfo.bad.length}
							Not a PDF, .txt or .md file: {docInfo.bad.join(', ')}
						{:else}
							{docInfo.n} document{docInfo.n === 1 ? '' : 's'} ({docInfo.mb.toFixed(1)} MB). Grounded jobs take a little
							longer per question.
						{/if}
					</p>
				{/if}
			</div>
			<div>
				<label class="label" for="count">How many questions?</label>
				<input id="count" class="input w-40" type="number" name="count" min="1" max={data.maxCount} bind:value={count} required />
				<p class="mt-1.5 text-xs text-zinc-500" id="generate-estimate">
					Estimated time: <span class="text-zinc-300">{estimate}</span> (about {perItemLo.toFixed(1)}–{perItemHi.toFixed(1)} seconds per
					question{docInfo && !docInfo.bad.length ? ' with source documents' : ''}). That includes the answer check: the Teacher
					re-solves every question on its own and replaces any whose answers disagree. You can close this page; the job runs
					in the queue and you can pause it.
				</p>
			</div>
			<button class="btn-primary" type="submit" disabled={submitting || Boolean(docInfo?.bad.length)}>{submitting ? 'Submitting…' : 'Submit'}</button>
		</form>
	</section>
{:else}
	<section class="mt-2 max-w-2xl">
		<h2 class="text-lg font-semibold text-zinc-100">This step: Train</h2>
		<p class="mt-2 text-sm text-zinc-400">
			Moonshine sets aside 20% of your questions for testing, trains the student on the rest, compares the student's
			answers before and after training, and then creates a new Ollama model named after you. While it trains it saves a
			checkpoint about every tenth of the way, scores each one on held-out questions, and keeps the best one.
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
				<summary class="cursor-pointer text-sm text-zinc-300">Trainer <span class="text-zinc-500">(advanced settings; leave blank for automatic)</span></summary>
				<p class="mt-3 text-xs text-zinc-400">
					These settings control how the Trainer teaches your Student model. Every box can be left blank; blank means
					Moonshine uses the default shown.
				</p>
				<div class="mt-3 grid gap-x-5 gap-y-4 sm:grid-cols-2">
					{#each TRAINER_FIELDS as f (f.name)}
						<div>
							<label class="text-xs font-medium text-zinc-300" for="adv-{f.name}">{f.label}</label>
							<input id="adv-{f.name}" class="input mt-1" type="number" name={f.name} min={f.min} step={f.step} placeholder={f.placeholder} aria-describedby="adv-{f.name}-help" />
							<p id="adv-{f.name}-help" class="mt-1.5 text-xs leading-5 text-zinc-500">{f.help}</p>
						</div>
					{/each}
				</div>
			</details>
			<button class="btn-primary" type="submit" disabled={submitting || !student || (pairInfo !== null && pairInfo.valid < 10)}>
				{submitting ? 'Submitting…' : 'Train'}
			</button>
		</form>
	</section>
{/if}

<script lang="ts">
	import HowMoonshineWorks from '$lib/components/HowMoonshineWorks.svelte';
	import { invalidateAll, goto } from '$app/navigation';
	import { page } from '$app/stores';
	import JobControls from '$lib/components/JobControls.svelte';
	import ProgressBar from '$lib/components/ProgressBar.svelte';
	import RunActivity from '$lib/components/RunActivity.svelte';
	import LossChart from '$lib/components/LossChart.svelte';
	import MiniMarkdown from '$lib/components/MiniMarkdown.svelte';
	import { computeAdvice } from '$lib/advice';
	import { formatDuration, formatTimestamp, statusBadgeClass } from '$lib/format';
	import { DOC_ROLE_LABELS, JOB_TYPE_LABELS, STATUS_LABELS, type JobStatus } from '$lib/types';
	import { pushToast } from '$lib/toast.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	const job = $derived(data.job);
	const r = $derived(data.report as any);
	const gguf = $derived(data.gguf);
	const ggufArtifact = $derived(data.artifacts.find((a) => a.key === 'ollama-gguf'));
	const advice = $derived(job.type === 'train' ? computeAdvice(r, data.curve?.valid ?? []) : null);
	const best = $derived(data.curve?.best ?? null);
	const tset = $derived(data.curve?.settings ?? null);
	const chk = $derived(data.quality?.check ?? null);
	const variety = $derived(data.quality?.variety ?? null);
	const REASON_LABELS: Record<string, string> = {
		letter_mismatch: 'different choice letter',
		number_mismatch: 'different number',
		judge_different: 'different answer (Teacher judged)',
		no_answer: 'no clear answer when re-solved',
		invalid_question: 'question has no single correct answer',
		not_in_source: 'answer not in the source section',
		wrong_refusal: 'said "I don\'t know" but the source section has the answer',
		unsupported_refusal: '"I don\'t know" answer added facts the source does not support',
		no_final_answer_in_completion: 'no final answer in the completion'
	};
	const teacherAdvice = $derived(data.advice?.markdown ? data.advice : null);
	// Downloads: a couple of main buttons, everything else under "Advanced files"
	const MAIN_KEYS = ['ollama-gguf', 'report-md', 'questions', 'sources'];
	const ADVANCED_INFO: Record<string, string> = {
		'report-json': 'The same results as the report, as data (for scripts and spreadsheets).',
		adapters: 'A small add-on to the original Qwen3 base model; needs that base model to run (for developers).',
		fused: "The full trained model for Apple's mlx-lm on Apple Silicon Macs. Not for Ollama.",
		log: 'The step-by-step record of this job, for troubleshooting.',
		chunks: 'The sections the source documents were split into, one per line.',
		questions: 'The generated questions and answers (.jsonl).',
		sources: 'Which document section each question came from, one line per question.',
		rejected: 'Questions the answer check dropped, with the reason, the answer key and the re-solved answer (one per line).'
	};
	const mainArtifacts = $derived(
		MAIN_KEYS.map((k) => data.artifacts.find((a) => a.key === k)).filter((a): a is NonNullable<typeof a> => Boolean(a))
	);
	const advancedArtifacts = $derived(data.artifacts.filter((a) => !MAIN_KEYS.includes(a.key)));
	const live = $derived(job.status === 'running' || job.status === 'queued' || Boolean(job.pendingAction));
	const gb = (b: number | null | undefined) => (b ? `${(b / 1e9).toFixed(1)} GB` : '');
	let logEl: HTMLPreElement | undefined = $state();
	let follow = $state(true);

	$effect(() => {
		const t = setInterval(() => invalidateAll(), live ? 3000 : 30000);
		return () => clearInterval(t);
	});
	$effect(() => {
		data.logTail;
		if (follow && logEl) logEl.scrollTop = logEl.scrollHeight;
	});

	const pct = (v: number | null | undefined) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`);
	const num = (v: number | null | undefined, d = 2) => (v == null ? '—' : Number(v).toFixed(d));

	let retrying = $state(false);
	async function retry(overrides: Record<string, string | number> = {}) {
		retrying = true;
		try {
			const res = await fetch(`/api/jobs/${job.id}/retry`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ overrides })
			});
			const d = await res.json();
			if (!res.ok || !d.ok) throw new Error(d.error ?? 'Retry failed');
			pushToast(d.jobId === job.id ? 'Retrying from saved progress.' : 'Submitted a new job with the suggested settings.');
			if (d.jobId !== job.id) goto(`/jobs/${d.jobId}`);
			else setTimeout(() => invalidateAll(), 1500);
		} catch (e) {
			pushToast(e instanceof Error ? e.message : String(e), 'error');
		} finally {
			retrying = false;
		}
	}
</script>

<svelte:head><title>{JOB_TYPE_LABELS[job.type]} — Moonshine</title></svelte:head>

<HowMoonshineWorks />

<a href="/jobs" class="text-sm text-zinc-500 hover:text-zinc-300">← All jobs</a>

{#if $page.url.searchParams.get('submitted')}
	<div class="mt-3 rounded-lg border border-emerald-900 bg-emerald-950/40 px-4 py-3 text-sm text-emerald-100">
		Submitted. The job is in the queue; this page updates by itself.
	</div>
{/if}

<div class="mt-3 flex flex-wrap items-start justify-between gap-4">
	<div>
		<h1 class="text-2xl font-semibold text-zinc-100">{JOB_TYPE_LABELS[job.type]}</h1>
		<p class="mt-1 text-sm text-zinc-400">
			by <span class="text-zinc-200">{job.ownerName}</span> · <span class="font-mono">{job.id}</span> · input
			<span class="font-mono">{job.inputName}</span>
		</p>
	</div>
	<div class="flex items-center gap-3">
		<span class={statusBadgeClass(job.status)}>{STATUS_LABELS[job.status as JobStatus]}</span>
		<JobControls id={job.id} status={job.status} owner={job.ownerName} pending={job.pendingAction} />
	</div>
</div>

{#if job.status === 'paused'}
	<div class="mt-4 rounded-lg border border-violet-800 bg-violet-950/40 px-4 py-3 text-sm text-violet-100">
		This job is paused and is <strong>holding the queue</strong>: no other job starts until it is resumed or cancelled.
		Resume continues from its saved progress.
	</div>
{:else if job.status === 'queued'}
	<div class="mt-4 rounded-lg border border-blue-900 bg-blue-950/40 px-4 py-3 text-sm text-blue-100">
		Waiting in line{job.queuePosition ? `: #${job.queuePosition}` : ''}.
		{#if data.queue.blockedByPause}The queue is on hold because another job is paused.{/if}
	</div>
{/if}

<RunActivity type={job.type} status={job.status} progress={job.progress} docNames={job.docNames} />

<div class="mt-5 grid gap-4 md:grid-cols-3">
	<div class="card p-4 md:col-span-2">
		<h2 class="label">Progress</h2>
		<ProgressBar progress={job.progress} status={job.status} />
	</div>
	<div class="card space-y-1 p-4 text-sm text-zinc-400">
		<p>Submitted: <span class="text-zinc-200">{formatTimestamp(job.submittedAt)}</span></p>
		<p>Started: <span class="text-zinc-200">{formatTimestamp(job.startedAt)}</span></p>
		{#if job.completedAt}<p>Finished: <span class="text-zinc-200">{formatTimestamp(job.completedAt)}</span></p>{/if}
		{#if job.startedAt && job.status !== 'queued'}<p>Elapsed: <span class="text-zinc-200">{formatDuration(job.startedAt, job.completedAt)}</span></p>{/if}
		{#if job.type === 'generate'}<p>Questions requested: <span class="text-zinc-200">{job.params.COUNT}</span></p>{/if}
		{#if job.type === 'train'}<p>Student: <span class="text-zinc-200">{job.params.STUDENT}</span></p>{/if}
		{#if job.type === 'train' && tset}
			<p>
				Training: <span class="text-zinc-200">{tset.iters?.toLocaleString()} steps</span>
				<span class="text-zinc-500">({tset.itersUser ? 'your setting' : `${tset.epochs} pass${tset.epochs === 1 ? '' : 'es'}${tset.epochsUser ? ', your setting' : tset.epochsDefault === 1 ? ', default for 1,000+ items' : ''}`})</span>
			</p>
		{/if}
		{#if best}
			<p>Checkpoint kept: <span class="text-zinc-200">step {best.step.toLocaleString()} of {best.finalStep.toLocaleString()}</span></p>
		{/if}
	</div>
</div>

{#if job.type === 'generate' && chk}
	<section class="card mt-5 p-4" id="answer-check">
		<h2 class="label">Answer check</h2>
		<p class="text-sm text-zinc-400">
			The Teacher re-solved every question on its own, without seeing the answer it wrote, and kept only the ones where
			both answers agree.
		</p>
		<div class="mt-3 flex flex-wrap gap-x-8 gap-y-2 text-sm">
			<p><span class="text-2xl font-semibold text-zinc-100">{chk.checked.toLocaleString()}</span> <span class="text-zinc-400">checked</span></p>
			<p><span class="text-2xl font-semibold text-amber-300">{chk.dropped.toLocaleString()}</span> <span class="text-zinc-400">dropped</span></p>
			<p><span class="text-2xl font-semibold text-emerald-300">{chk.replaced.toLocaleString()}</span> <span class="text-zinc-400">replaced</span></p>
			<p><span class="text-2xl font-semibold text-zinc-100">{chk.dropRate == null ? '—' : `${(chk.dropRate * 100).toFixed(1)}%`}</span> <span class="text-zinc-400">drop rate</span></p>
			<p id="refusals-kept"><span class="text-2xl font-semibold text-sky-300">{chk.refusalsKept.toLocaleString()}</span> <span class="text-zinc-400">"I don't know" answers kept</span></p>
		</div>
		<p class="mt-2 text-xs text-zinc-500">
			"I don't know" answers are questions your documents don't answer, where the answer says so and points to the right
			person or place. They are kept when the Teacher confirms the source really doesn't have that information{chk.refusalsChecked
				? ` (${chk.refusalsKept.toLocaleString()} of ${chk.refusalsChecked.toLocaleString()} kept)`
				: ''}.{#if chk.supportKept}
				{' '}{chk.supportKept.toLocaleString()} other answer{chk.supportKept === 1 ? ' was' : 's were'} kept after the Teacher found {chk.supportKept === 1 ? 'it' : 'them'} in the source on a second look.{/if}
		</p>
		{#if Object.keys(chk.reasons).length}
			<p class="mt-2 text-xs text-zinc-500">
				Why they were dropped: {Object.entries(chk.reasons).map(([k, v]) => `${v} × ${REASON_LABELS[k] ?? k}`).join(' · ')}.
				Details are in <a class="underline" href="#downloads">Advanced files</a> (Dropped by the answer check).
			</p>
		{/if}
		{#if chk.nearExactDropped}
			<p class="mt-1 text-xs text-zinc-500">Also removed {chk.nearExactDropped} near-exact repeat{chk.nearExactDropped === 1 ? '' : 's'} before checking.</p>
		{/if}
		{#if chk.warning}
			<p role="alert" class="mt-3 rounded-md border border-amber-800 bg-amber-950/40 px-3 py-2 text-sm text-amber-100">{chk.warning}</p>
		{/if}
		{#if variety}
			{#if variety.warn}
				<div role="alert" class="mt-3 rounded-md border border-amber-800 bg-amber-950/40 px-3 py-2 text-sm text-amber-100" id="variety-warning">
					<strong>Low variety:</strong> {variety.nearDuplicates.toLocaleString()} of {variety.items.toLocaleString()} questions
					({(variety.rate * 100).toFixed(1)}%) are near-duplicates of an earlier one. Ask for more varied subtopics, question
					types, numbers and scenarios in your PROMPT (the Generate Prompt interview now asks about this).
					{#if variety.examples.length}
						<details class="mt-1">
							<summary class="cursor-pointer text-xs text-amber-200">Examples</summary>
							<ul class="mt-1 space-y-1 text-xs text-amber-100/80">
								{#each variety.examples as e (e.i)}<li>#{e.i + 1}: “{e.prompt}” ≈ #{e.similar_to + 1}: “{e.other}”</li>{/each}
							</ul>
						</details>
					{/if}
				</div>
			{:else}
				<p class="mt-2 text-xs text-zinc-500">
					Variety: {variety.nearDuplicates.toLocaleString()} near-duplicate{variety.nearDuplicates === 1 ? '' : 's'} out of {variety.items.toLocaleString()} ({(variety.rate * 100).toFixed(1)}%; warning above 5%).
				</p>
			{/if}
		{/if}
	</section>
{/if}

{#if job.type === 'generate' && job.docNames.length}
	<section class="card mt-5 p-4" id="source-documents">
		<h2 class="label">Source documents</h2>
		<p class="text-sm text-zinc-400">
			Grounded job: the Teacher wrote each batch from one section of these documents. The Student never sees them.
		</p>
		<ul class="mt-2 space-y-1 text-sm">
			{#each job.docNames as name, i (i)}
				{@const d = data.documents?.docs.find((x) => x.name === name)}
				{@const nq = data.documents?.questionsByDoc ? (data.documents.questionsByDoc[name] ?? 0) : undefined}
				<li class="flex flex-wrap items-baseline gap-x-2">
					<span class="font-mono text-zinc-200">{name}</span>
					{#if job.docRoles?.[i]}<span class="rounded bg-bg-subtle px-1.5 text-xs text-zinc-400">{DOC_ROLE_LABELS[job.docRoles[i]]}</span>{/if}
					{#if nq !== undefined}<span class="text-xs text-emerald-300">{nq.toLocaleString()} question{nq === 1 ? '' : 's'}</span>{/if}
					{#if d}
						<span class="text-xs {d.error ? 'text-red-400' : 'text-zinc-500'}">
							{#if d.error}{d.error}{:else}{d.pages ? `${d.pages} pages · ` : ''}{(d.words ?? 0).toLocaleString()} words · {d.chunks} sections{/if}
						</span>
					{/if}
				</li>
			{/each}
		</ul>
		{#if data.documents}
			<p class="mt-2 text-sm text-zinc-300">
				{data.documents.chunks} sections of up to about 1,500 words{#if data.documents.covered !== null}; {data.documents.covered} used{job.status === 'completed' ? '' : ' so far'}{/if}.
				The question counts show which document each question was mainly written from. Each question's section is listed
				in the Sources download (one line per question, in the same order).
			</p>
		{:else}
			<p class="mt-2 text-xs text-zinc-500">The documents are split into sections when the job starts.</p>
		{/if}
	</section>
{/if}

{#if job.status === 'failed'}
	<section class="card mt-5 border-red-900 p-4">
		<h2 class="text-sm font-semibold text-red-300">This job failed</h2>
		{#if job.error}<p class="mt-1 font-mono text-xs text-red-200">{job.error}</p>{/if}
		{#if data.diagnosis?.diagnosisMarkdown}
			<div class="mt-3 rounded-md bg-bg-subtle p-3">
				<p class="label">AI diagnosis</p>
				<pre class="whitespace-pre-wrap text-sm text-zinc-300">{data.diagnosis.diagnosisMarkdown}</pre>
			</div>
		{:else}
			<p class="mt-2 text-xs text-zinc-500">An AI diagnosis may appear here in a minute or two.</p>
		{/if}
		<div class="mt-3 flex flex-wrap gap-2">
			{#if data.isOwner}<button class="btn-primary" disabled={retrying} onclick={() => retry()}>Retry from saved progress</button>{/if}
			{#if data.diagnosis?.suggestedRetryParams && Object.keys(data.diagnosis.suggestedRetryParams).length}
				<button class="btn-secondary" disabled={retrying} onclick={() => retry(data.diagnosis!.suggestedRetryParams!)}>
					Retry as a new job with {Object.entries(data.diagnosis.suggestedRetryParams).map(([k, v]) => `${k}=${v}`).join(', ')}
				</button>
			{/if}
		</div>
	</section>
{/if}

{#if job.type === 'train' && (job.ollamaTag || r)}
	<section class="card mt-5 p-5">
		<h2 class="text-lg font-semibold text-zinc-100">Results</h2>
		{#if job.ollamaTag}
			<p class="mt-2 text-sm text-zinc-400">
				New Ollama model: <span class="rounded bg-bg-subtle px-2 py-0.5 font-mono text-emerald-300">{job.ollamaTag}</span>
				<span class="ml-2 text-xs text-zinc-500">Try it: <span class="font-mono">ollama run {job.ollamaTag} --think=false</span></span>
			</p>
		{/if}
		{#if r}
			<p class="mt-3 text-sm text-zinc-400">
				Tested on {r.data?.n_eval} held-out questions it never trained on ({r.data?.n_train} used for training). The teacher graded each answer.
			</p>
			<div class="mt-3 overflow-x-auto">
				<table class="text-sm">
					<thead>
						<tr class="text-left text-xs uppercase tracking-wide text-zinc-500">
							<th class="py-2 pr-6"></th><th class="py-2 pr-6">Before training</th><th class="py-2 pr-6">After training</th>
						</tr>
					</thead>
					<tbody class="text-zinc-200">
						<tr><td class="py-1.5 pr-6 text-zinc-400">Final answer correct</td><td class="pr-6">{pct(r.untrained?.accuracy)}</td><td class="pr-6 font-semibold text-emerald-300">{pct(r.trained?.accuracy)}</td></tr>
						<tr><td class="py-1.5 pr-6 text-zinc-400">Average reasoning score (1–5)</td><td class="pr-6">{num(r.untrained?.avg_reasoning_score)}</td><td class="pr-6">{num(r.trained?.avg_reasoning_score)}</td></tr>
						<tr><td class="py-1.5 pr-6 text-zinc-400">Validation loss (lower is better)</td><td class="pr-6">{num(r.validation_loss?.base?.loss, 3)}</td><td class="pr-6">{num(r.validation_loss?.trained?.loss, 3)}</td></tr>
					</tbody>
				</table>
			</div>
			{#if best}
				<p class="mt-3 text-sm text-zinc-400" id="best-checkpoint">
					{#if best.used === 'best'}
						Checkpoint kept: <span class="font-semibold text-zinc-100">step {best.step.toLocaleString()} of {best.finalStep.toLocaleString()}</span>,
						the one with the lowest validation loss during training ({num(best.valLoss, 3)}; the final step had {num(best.finalValLoss, 3)}).
						The results above, the Ollama model and the download all use it.
					{:else}
						Checkpoint kept: <span class="font-semibold text-zinc-100">the final step ({best.finalStep.toLocaleString()})</span>, which had the lowest validation loss{best.valLoss != null ? ` (${num(best.valLoss, 3)})` : ''}.
					{/if}
				</p>
			{/if}
			{#if advice}
				<div class="mt-5 rounded-lg border border-border-subtle bg-bg-subtle/40 p-4" id="raise-score">
					<h3 class="text-base font-semibold text-zinc-100">How to raise the Final answer correct score</h3>
					<p class="mt-1 text-sm text-zinc-400">
						After training: <span class="font-semibold text-emerald-300">{advice.trainedPct}</span>
						{#if advice.grade}≈ <span class="rounded bg-bg-subtle px-1.5 py-0.5 font-semibold text-zinc-100">{advice.grade}</span>{/if}
						· before: {advice.basePct}{#if advice.baseGrade}{' '}({advice.baseGrade}){/if}
						{#if advice.changePts != null}· change {advice.changePts >= 0 ? '+' : '−'}{Math.abs(advice.changePts).toFixed(1)} points{#if advice.noisePts != null}{' '}(rough noise band about ±{advice.noisePts.toFixed(1)}){/if}{/if}
					</p>
					{#if advice.settings}<p class="mt-0.5 text-xs text-zinc-500">Settings used: {advice.settings}</p>{/if}
					{#if advice.tips.length}
						<ol class="mt-3 space-y-3">
							{#each advice.tips as tip, i (tip.id)}
								<li class="text-sm">
									<p class="font-medium text-zinc-100">{i + 1}. {tip.title}</p>
									<p class="mt-0.5 text-zinc-300">{tip.action}</p>
									<p class="mt-0.5 text-xs text-zinc-500"><span class="font-semibold text-zinc-400">Why:</span> {tip.why}</p>
								</li>
							{/each}
						</ol>
					{:else}
						<p class="mt-3 text-sm text-zinc-300">No obvious problems in the numbers. Add more, harder questions on the topics it still misses.</p>
					{/if}
					{#if teacherAdvice}
						<div class="mt-5 border-t border-border-subtle pt-4" id="teacher-analysis">
							<h3 class="text-base font-semibold text-zinc-100">Teacher's analysis of missed questions</h3>
							<p class="mb-2 mt-0.5 text-xs text-zinc-500">
								{teacherAdvice.teacher ?? 'The Teacher'} read {teacherAdvice.nSampled ?? '?'} of the {teacherAdvice.nWrong ?? '?'} held-out questions the trained model got wrong.
							</p>
							<MiniMarkdown text={teacherAdvice.markdown ?? ''} />
						</div>
					{/if}
				</div>
			{/if}
			{#if r.examples?.length}
				<details class="mt-4">
					<summary class="cursor-pointer text-sm text-zinc-300">Example answers ({r.examples.length})</summary>
					{#each r.examples as ex, i (i)}
						<div class="mt-3 rounded-md border border-border-subtle p-3 text-xs">
							<p class="label">Question</p>
							<pre class="whitespace-pre-wrap text-zinc-200">{ex.prompt}</pre>
							<div class="mt-2 grid gap-3 md:grid-cols-3">
								<div><p class="label">Reference</p><pre class="whitespace-pre-wrap text-zinc-400">{ex.reference}</pre></div>
								<div><p class="label">Before ({ex.untrained_grade?.correct ? 'correct' : 'incorrect'})</p><pre class="whitespace-pre-wrap text-zinc-400">{ex.untrained_answer?.slice(0, 1200)}</pre></div>
								<div><p class="label">After ({ex.trained_grade?.correct ? 'correct' : 'incorrect'})</p><pre class="whitespace-pre-wrap text-zinc-400">{ex.trained_answer?.slice(0, 1200)}</pre></div>
							</div>
						</div>
					{/each}
				</details>
			{/if}
		{/if}
	</section>
{/if}

{#if data.curve && data.curve.train.length > 1}
	<section class="card mt-5 p-5">
		<h2 class="label">Training curve</h2>
		<LossChart train={data.curve.train} valid={data.curve.valid} totalSteps={data.curve.totalSteps} bestStep={best && best.used === 'best' ? best.step : null} />
		{#if best}
			<p class="mt-1 text-xs text-zinc-500">
				Checkpoints were saved every {best.saveEvery ?? '?'} steps and scored on {best.valItems ?? '?'} held-out questions; step {best.step.toLocaleString()} was kept.
			</p>
		{/if}
	</section>
{/if}

{#if data.artifacts.length || (job.type === 'train' && gguf?.state === 'failed' && gguf.error)}
	<section class="card mt-5 p-5" id="downloads">
		<h2 class="label">Downloads</h2>
		{#if mainArtifacts.length}
			<div class="flex flex-wrap items-center gap-2">
				{#each mainArtifacts as a (a.key)}
					<a class={a.key === 'ollama-gguf' ? 'btn-primary' : 'btn-secondary'} href="/jobs/{job.id}/download?file={a.key}" download>{a.label}</a>
				{/each}
				{#if ggufArtifact && gguf?.bytes}<span class="text-xs text-zinc-500">Ollama .zip: {gb(gguf.bytes)}</span>{/if}
			</div>
		{/if}
		{#if ggufArtifact}
			<p class="mt-2 text-sm text-zinc-400" id="ollama-download">
				To use it on your own computer: install <a class="underline" href="https://ollama.com/download" target="_blank" rel="noreferrer">Ollama</a>, unzip, then in that folder run
				<span class="font-mono text-zinc-200">ollama create {gguf?.modelName || 'my-model'} -f Modelfile</span> and
				<span class="font-mono text-zinc-200">ollama run {gguf?.modelName || 'my-model'}</span> (README.txt inside has Windows steps).
			</p>
		{:else if job.type === 'train' && gguf?.state === 'failed' && gguf.error}
			<p class="mt-2 text-xs text-zinc-500">The Ollama download could not be built for this job: <span class="font-mono">{gguf.error.slice(0, 200)}</span></p>
		{/if}
		{#if advancedArtifacts.length}
			<details class="mt-4">
				<summary class="cursor-pointer text-sm text-zinc-300">Advanced files ({advancedArtifacts.length})</summary>
				<ul class="mt-3 space-y-2">
					{#each advancedArtifacts as a (a.key)}
						<li class="flex flex-wrap items-center gap-x-3 gap-y-1">
							<a class="btn-secondary px-2 py-1 text-xs" href="/jobs/{job.id}/download?file={a.key}" download>{a.label}{a.kind === 'dir' ? ' (.tar.gz)' : ''}</a>
							{#if ADVANCED_INFO[a.key]}<span class="text-xs text-zinc-500">{ADVANCED_INFO[a.key]}</span>{/if}
						</li>
					{/each}
				</ul>
			</details>
		{/if}
	</section>
{/if}

<section class="card mt-5 p-5">
	<div class="flex items-center justify-between">
		<h2 class="label">Log (last 200 lines)</h2>
		<label class="flex items-center gap-2 text-xs text-zinc-500"><input type="checkbox" bind:checked={follow} /> Follow</label>
	</div>
	<pre bind:this={logEl} class="max-h-[420px] overflow-auto whitespace-pre-wrap rounded-md bg-black/40 p-3 font-mono text-xs leading-5 text-zinc-300">{data.logTail || 'No output yet.'}</pre>
</section>

<script lang="ts">
	import { invalidateAll, goto } from '$app/navigation';
	import { page } from '$app/stores';
	import JobControls from '$lib/components/JobControls.svelte';
	import ProgressBar from '$lib/components/ProgressBar.svelte';
	import LossChart from '$lib/components/LossChart.svelte';
	import { formatDuration, formatTimestamp, statusBadgeClass } from '$lib/format';
	import { JOB_TYPE_LABELS, STATUS_LABELS, type JobStatus } from '$lib/types';
	import { pushToast } from '$lib/toast.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	const job = $derived(data.job);
	const r = $derived(data.report as any);
	const live = $derived(job.status === 'running' || job.status === 'queued' || Boolean(job.pendingAction));
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
		<JobControls id={job.id} status={job.status} pending={job.pendingAction} />
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
	</div>
</div>

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
			<button class="btn-primary" disabled={retrying} onclick={() => retry()}>Retry from saved progress</button>
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
		<LossChart train={data.curve.train} valid={data.curve.valid} totalSteps={data.curve.totalSteps} />
	</section>
{/if}

{#if data.artifacts.length}
	<section class="card mt-5 p-5">
		<h2 class="label">Downloads</h2>
		<div class="flex flex-wrap gap-2">
			{#each data.artifacts as a (a.key)}
				<a class="btn-secondary" href="/jobs/{job.id}/download?file={a.key}" download>{a.label}{a.kind === 'dir' ? ' (.tar.gz)' : ''}</a>
			{/each}
		</div>
	</section>
{/if}

<section class="card mt-5 p-5">
	<div class="flex items-center justify-between">
		<h2 class="label">Log (last 200 lines)</h2>
		<label class="flex items-center gap-2 text-xs text-zinc-500"><input type="checkbox" bind:checked={follow} /> Follow</label>
	</div>
	<pre bind:this={logEl} class="max-h-[420px] overflow-auto whitespace-pre-wrap rounded-md bg-black/40 p-3 font-mono text-xs leading-5 text-zinc-300">{data.logTail || 'No output yet.'}</pre>
</section>

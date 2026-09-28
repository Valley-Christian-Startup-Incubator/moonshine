<script lang="ts">
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import { pushToast } from '$lib/toast.svelte';
	import type { ActionData, PageData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();

	type Preview = { name: string; jobs: number; activeJobs: number; resultFolders: number; ollamaModels: string[] };
	let dialog: HTMLDialogElement | undefined = $state();
	let preview = $state<Preview | null>(null);
	let confirmed = $state(false);
	let busy = $state(false);

	async function openDelete(name: string) {
		preview = null;
		confirmed = false;
		dialog?.showModal();
		const res = await fetch(`/admin/delete-user?name=${encodeURIComponent(name)}`);
		const d = await res.json();
		if (!res.ok) {
			dialog?.close();
			pushToast(d.error ?? 'Could not load details.', 'error');
			return;
		}
		preview = d;
	}

	async function doDelete() {
		if (!preview || !confirmed) return;
		busy = true;
		try {
			const res = await fetch('/admin/delete-user', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ name: preview.name })
			});
			const d = await res.json();
			if (!res.ok || !d.ok) throw new Error(d.error ?? 'Delete failed');
			pushToast(
				`Deleted ${d.name}: ${d.jobsPurged} job(s), ${d.ollamaModelsDeleted.length} Ollama model(s), ${d.logScrub?.linesRemoved ?? 0} log line(s).` +
					(d.ollamaModelsFailed.length ? ` Could not delete: ${d.ollamaModelsFailed.join(', ')}` : '')
			);
			dialog?.close();
			await invalidateAll();
		} catch (e) {
			pushToast(e instanceof Error ? e.message : String(e), 'error');
		} finally {
			busy = false;
		}
	}
</script>

<svelte:head><title>Admin — Moonshine</title><meta name="robots" content="noindex" /></svelte:head>

{#if !data.authed}
	<div class="mx-auto mt-10 max-w-sm">
		<h1 class="text-xl font-semibold text-zinc-100">Admin</h1>
		{#if form?.error}<p role="alert" class="mt-3 rounded-md bg-red-950 px-3 py-2 text-sm text-red-300">{form.error}</p>{/if}
		<form method="POST" action="?/login" use:enhance class="mt-4 space-y-3">
			<input class="input" type="password" name="password" placeholder="Admin password" autocomplete="current-password" required />
			<button class="btn-primary w-full">Sign in</button>
		</form>
	</div>
{:else}
	<div class="flex items-center justify-between">
		<h1 class="text-xl font-semibold text-zinc-100">Admin: names</h1>
		<form method="POST" action="?/logout" use:enhance><button class="btn-secondary">Leave admin</button></form>
	</div>
	<p class="mt-2 text-sm text-zinc-400">Deleting a name also deletes everything that person made: jobs, result files and their Ollama models, and removes log lines that mention them.</p>
	<div class="card mt-4 divide-y divide-border-subtle">
		{#each data.users as u (u.name)}
			<div class="flex items-center justify-between px-4 py-3">
				<div>
					<p class="text-sm text-zinc-100">{u.name}</p>
					<p class="text-xs text-zinc-500">added {new Date(u.createdAt).toLocaleDateString()}</p>
				</div>
				<button class="btn-danger" onclick={() => openDelete(u.name)}>Delete</button>
			</div>
		{:else}
			<p class="px-4 py-6 text-sm text-zinc-500">No names yet.</p>
		{/each}
	</div>
{/if}

<dialog bind:this={dialog} class="w-full max-w-md rounded-lg border border-red-900 bg-bg-elevated p-0 text-zinc-200 backdrop:bg-black/70">
	<div class="p-5">
		{#if !preview}
			<p class="text-sm text-zinc-400">Loading…</p>
		{:else}
			<h2 class="text-lg font-semibold text-red-300">Delete "{preview.name}"?</h2>
			<p class="mt-2 text-sm text-zinc-300">This permanently deletes:</p>
			<ul class="mt-2 list-disc space-y-1 pl-5 text-sm text-zinc-300">
				<li>the name <strong>{preview.name}</strong></li>
				<li><strong>{preview.jobs}</strong> job(s){preview.activeJobs ? `, including ${preview.activeJobs} queued, running or paused (they will be cancelled first)` : ''}</li>
				<li><strong>{preview.resultFolders}</strong> result folder(s) (questions, adapters, reports, fused models)</li>
				<li>
					<strong>{preview.ollamaModels.length}</strong> Ollama model(s){preview.ollamaModels.length ? ':' : ''}
					{#if preview.ollamaModels.length}
						<ul class="mt-1 pl-2 font-mono text-xs text-zinc-400">
							{#each preview.ollamaModels as t (t)}<li>{t}</li>{/each}
						</ul>
					{/if}
				</li>
			</ul>
			<p class="mt-3 text-sm text-zinc-400">This cannot be undone. Base models are never touched.</p>
			<label class="mt-4 flex items-center gap-2 text-sm text-zinc-200">
				<input type="checkbox" bind:checked={confirmed} /> Yes, delete {preview.name} and everything listed above
			</label>
		{/if}
		<div class="mt-5 flex justify-end gap-2">
			<button class="btn-secondary" onclick={() => dialog?.close()} disabled={busy}>Keep</button>
			<button class="inline-flex items-center rounded-md bg-red-700 px-4 py-2 text-sm font-medium text-white hover:bg-red-600 disabled:opacity-40" onclick={doDelete} disabled={!preview || !confirmed || busy}>
				{busy ? 'Deleting…' : 'Delete permanently'}
			</button>
		</div>
	</div>
</dialog>

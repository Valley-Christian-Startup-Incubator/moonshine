<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { page } from '$app/stores';
	import { pushToast } from '$lib/toast.svelte';

	let {
		id,
		status,
		owner,
		pending = null,
		compact = false
	}: { id: string; status: string; owner: string; pending?: string | null; compact?: boolean } = $props();
	// Only the job's owner gets Pause / Resume / Cancel (the server enforces this too, with a 403).
	const isOwner = $derived(Boolean($page.data.userName) && $page.data.userName === owner);
	let busy = $state(false);

	async function act(action: 'pause' | 'resume' | 'cancel') {
		if (action === 'cancel' && !confirm('Cancel this job? It will stop and leave the queue. Finished work stays downloadable, but the job cannot be resumed.')) return;
		busy = true;
		try {
			const res = await fetch(`/api/jobs/${id}/${action}`, { method: 'POST' });
			const data = await res.json().catch(() => ({}));
			if (!res.ok || data.ok === false) throw new Error(data.error ?? `Could not ${action} the job.`);
			pushToast(
				action === 'pause'
					? 'Pausing… the job stops at the next safe point.'
					: action === 'resume'
						? 'Resumed. It continues from its saved progress.'
						: 'Cancelling…'
			);
			setTimeout(() => invalidateAll(), 1500);
		} catch (e) {
			pushToast(e instanceof Error ? e.message : String(e), 'error');
		} finally {
			busy = false;
		}
	}
	const cls = $derived(compact ? 'px-2 py-1 text-xs' : '');
</script>

<div class="flex flex-wrap gap-2">
	{#if !isOwner}
		{#if pending}<span class="text-xs text-zinc-500">{pending === 'pause' ? 'Pausing…' : pending === 'cancel' || pending === 'purge' ? 'Cancelling…' : pending === 'resume' ? 'Resuming…' : ''}</span>{/if}
	{:else if pending}
		<span class="text-xs text-zinc-500">{pending === 'pause' ? 'Pausing…' : pending === 'cancel' || pending === 'purge' ? 'Cancelling…' : pending === 'resume' ? 'Resuming…' : ''}</span>
	{:else}
		{#if status === 'running'}
			<button type="button" class="btn-secondary {cls}" disabled={busy} onclick={() => act('pause')}>Pause</button>
		{/if}
		{#if status === 'paused'}
			<button type="button" class="btn-primary {cls}" disabled={busy} onclick={() => act('resume')}>Resume</button>
		{/if}
		{#if status === 'queued' || status === 'running' || status === 'paused'}
			<button type="button" class="btn-danger {cls}" disabled={busy} onclick={() => act('cancel')}>Cancel</button>
		{/if}
	{/if}
</div>

<script lang="ts">
	import { formatEta } from '$lib/format';
	let { progress, status, showPhase = true }: { progress: any; status: string; showPhase?: boolean } = $props();
</script>

{#if progress}
	<div class="min-w-[180px]">
		{#if showPhase && progress.phaseLabel && progress.phaseCount && progress.phaseCount > 1}
			<p class="text-xs text-zinc-400">Step {progress.phaseIndex}/{progress.phaseCount}: {progress.phaseLabel}</p>
		{/if}
		<div class="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-zinc-800">
			<div
				class="h-full rounded-full {status === 'paused' ? 'bg-violet-500' : status === 'failed' ? 'bg-red-500' : 'bg-emerald-500'}"
				style="width: {progress.percent ?? 0}%"
			></div>
		</div>
		<p class="mt-1 text-xs text-zinc-500">
			{progress.done.toLocaleString()}/{progress.total.toLocaleString()} {progress.unit}
			{#if status === 'running' && (progress.etaTotalSec ?? progress.etaSec) != null}
				· {formatEta(progress.etaTotalSec ?? progress.etaSec)}
			{/if}
		</p>
	</div>
{:else}
	<span class="text-xs text-zinc-600">—</span>
{/if}

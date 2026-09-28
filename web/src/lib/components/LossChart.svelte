<script lang="ts">
	let {
		train,
		valid,
		totalSteps,
		bestStep = null
	}: { train: [number, number][]; valid: [number, number][]; totalSteps: number | null; bestStep?: number | null } = $props();
	const W = 560;
	const H = 180;
	const P = 32;
	const maxX = $derived(Math.max(totalSteps ?? 0, ...train.map((p) => p[0]), ...valid.map((p) => p[0]), 1));
	const maxY = $derived(Math.max(...train.map((p) => p[1]), ...valid.map((p) => p[1]), 0.1));
	const x = (v: number) => P + (v / maxX) * (W - P - 8);
	const y = (v: number) => H - P + 8 - (v / maxY) * (H - P - 8);
	const line = $derived(train.map((p) => `${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(' '));
</script>

<svg viewBox="0 0 {W} {H}" class="w-full max-w-xl" role="img" aria-label="Training loss curve">
	<line x1={P} y1={H - P + 8} x2={W - 8} y2={H - P + 8} stroke="#3f3f46" />
	<line x1={P} y1={8} x2={P} y2={H - P + 8} stroke="#3f3f46" />
	<text x={P - 4} y={14} fill="#71717a" font-size="10" text-anchor="end">{maxY.toFixed(1)}</text>
	<text x={P - 4} y={H - P + 8} fill="#71717a" font-size="10" text-anchor="end">0</text>
	<text x={W - 8} y={H - 8} fill="#71717a" font-size="10" text-anchor="end">step {maxX}</text>
	<polyline points={line} fill="none" stroke="#34d399" stroke-width="1.5" />
	{#if bestStep != null}
		<line x1={x(bestStep)} y1={8} x2={x(bestStep)} y2={H - P + 8} stroke="#fbbf24" stroke-dasharray="3 3" />
		<text x={x(bestStep) + 3} y={18} fill="#fbbf24" font-size="10">kept: step {bestStep}</text>
	{/if}
	{#each valid as p, i (i)}
		<circle cx={x(p[0])} cy={y(p[1])} r="3" fill="#a78bfa" />
	{/each}
</svg>
<p class="text-xs text-zinc-500">
	<span class="text-emerald-400">—</span> training loss <span class="ml-3 text-violet-400">●</span> validation loss (lower is better)
	{#if bestStep != null}<span class="ml-3 text-amber-400">┆</span> checkpoint kept (lowest validation loss){/if}
</p>

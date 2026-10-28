<script lang="ts">
	type Stage = 'prompt' | 'questions' | 'train';
	let {
		current = null,
		onSelect = undefined
	}: { current?: Stage | null; onSelect?: (s: Stage) => void } = $props();

	const STEPS: { id: Stage; n: number; title: string }[] = [
		{ id: 'prompt', n: 1, title: 'Generate Prompt.' },
		{ id: 'questions', n: 2, title: 'Generate Questions.' },
		{ id: 'train', n: 3, title: 'Train.' }
	];

	function click(e: MouseEvent, s: Stage) {
		if (!onSelect) return;
		e.preventDefault();
		onSelect(s);
	}
</script>

<!-- On the Lab page (onSelect set) this panel is the step switcher, so it cannot be collapsed there. -->
<svelte:element
	this={onSelect ? 'section' : 'details'}
	open={onSelect ? undefined : true}
	aria-label={onSelect ? 'How Moonshine works: choose a step' : undefined}
	class="how-moonshine mb-6 rounded-lg border border-sky-900/70 bg-sky-950/20 px-4 py-3"
>
	{#if onSelect}
		<h1 class="text-base font-semibold text-zinc-100">How Moonshine works</h1>
	{:else}
		<summary class="cursor-pointer select-none text-base font-semibold text-zinc-100">How Moonshine works</summary>
	{/if}
	<p class="mt-2 text-sm text-zinc-300">Moonshine turns you into the trainer of your own small AI model in three steps:</p>
	<ol class="mt-3 grid gap-2 md:grid-cols-3">
		{#each STEPS as s (s.id)}
			{@const on = current === s.id}
			<li>
				<a
					href="/?stage={s.id}"
					onclick={(e) => click(e, s.id)}
					aria-current={on ? 'step' : undefined}
					class="block h-full rounded-md border px-3 py-2 text-sm transition-colors
						{on
						? 'border-sky-500 bg-sky-900/40 text-zinc-100 ring-1 ring-sky-500'
						: 'border-border-subtle bg-bg-subtle/60 text-zinc-400 hover:border-zinc-600 hover:text-zinc-200'}"
				>
					<span class="font-mono {on ? 'text-sky-300' : 'text-zinc-500'}">{s.n}.</span>
					<strong class={on ? 'text-sky-200' : 'text-zinc-200'}>{s.title}</strong>
					{#if s.id === 'prompt'}
						Answer an AI’s questions to create your training prompt.
					{:else if s.id === 'questions'}
						The teacher uses your prompt to write practice questions and answers.
					{:else}
						Train your small model on those examples, then test its answers.
					{/if}
					{#if on}<span class="mt-1 block text-xs font-semibold uppercase tracking-wide text-sky-400">You are here</span>{/if}
				</a>
			</li>
		{/each}
	</ol>
</svelte:element>

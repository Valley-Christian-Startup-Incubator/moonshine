<script lang="ts">
	import type { ActionData, PageData } from './$types';
	let { data, form }: { data: PageData; form: ActionData } = $props();
	let filter = $state('');
	const shown = $derived(
		data.users.filter((u) => u.name.toLowerCase().includes(filter.trim().toLowerCase()))
	);
</script>

<svelte:head><title>Who are you? — Moonshine</title></svelte:head>

<div class="mx-auto mt-6 max-w-xl">
	<p class="text-xs uppercase tracking-[0.25em] text-emerald-400">Moonshine</p>
	<h1 class="mt-3 text-3xl font-semibold text-zinc-100">Who's using Moonshine?</h1>
	<p class="mt-2 text-sm text-zinc-400">
		Pick your name so your jobs and models are labeled as yours. New here? Add your name below.
	</p>
	{#if data.current}
		<p class="mt-3 text-sm text-zinc-400">Currently: <span class="text-zinc-100">{data.current}</span></p>
	{/if}
	{#if form?.error}
		<p role="alert" class="mt-4 rounded-md bg-red-950 px-4 py-3 text-sm text-red-300">{form.error}</p>
	{/if}

	<section class="card mt-6 p-5">
		<h2 class="text-sm font-semibold text-zinc-200">Pick your name</h2>
		{#if data.users.length === 0}
			<p class="mt-2 text-sm text-zinc-500">No names yet. Add yours below.</p>
		{:else}
			{#if data.users.length > 8}
				<input class="input mt-3" placeholder="Search names…" bind:value={filter} aria-label="Search names" />
			{/if}
			<form method="POST" action="?/select" class="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
				<input type="hidden" name="next" value={data.next} />
				{#each shown as u (u.name)}
					<button
						type="submit"
						name="name"
						value={u.name}
						class="btn-secondary justify-start {u.name === data.current ? 'ring-1 ring-emerald-600' : ''}"
					>
						{u.name}
					</button>
				{/each}
			</form>
		{/if}
	</section>

	<section class="card mt-4 p-5">
		<h2 class="text-sm font-semibold text-zinc-200">Add a new name</h2>
		<form method="POST" action="?/add" class="mt-3 flex gap-2">
			<input type="hidden" name="next" value={data.next} />
			<input
				class="input"
				name="name"
				placeholder="First and last name"
				maxlength="40"
				required
				value={form && 'name' in form ? form.name : ''}
				aria-label="Your name"
			/>
			<button class="btn-primary shrink-0" type="submit">Add and continue</button>
		</form>
		<p class="mt-2 text-xs text-zinc-500">Your name is also used in the names of models you train.</p>
	</section>
</div>

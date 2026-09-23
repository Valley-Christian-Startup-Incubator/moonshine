<script lang="ts">
	import { enhance } from '$app/forms';
	import { page } from '$app/stores';
	import type { ActionData } from './$types';

	let { form }: { form: ActionData } = $props();
	let pending = $state(false);
</script>

<svelte:head><title>Sign in — Moonshine</title></svelte:head>
<div class="mx-auto mt-10 max-w-md rounded-2xl border border-border bg-bg-subtle p-8">
	<p class="text-xs uppercase tracking-[0.25em] text-emerald-400">Moonshine</p>
	<h1 class="mt-4 text-3xl font-semibold text-zinc-100">Sign in.</h1>
	<p class="mt-3 text-sm leading-6 text-zinc-400">
		Enter the shared password to use the model lab and view its jobs.
	</p>
	{#if form?.error}
		<p role="alert" class="mt-4 rounded-md bg-red-950 px-4 py-3 text-sm text-red-300">{form.error}</p>
	{/if}
	<form
		method="POST"
		use:enhance={() => {
			pending = true;
			return async ({ update }) => {
				try {
					await update();
				} finally {
					pending = false;
				}
			};
		}}
		class="mt-5 space-y-4"
	>
		<input type="hidden" name="next" value={$page.url.searchParams.get('next') ?? '/'} />
		<div>
			<label class="label" for="password">Password</label>
			<input
				id="password"
				name="password"
				type="password"
				class="input"
				autocomplete="current-password"
				maxlength="256"
				required
			/>
		</div>
		<button type="submit" class="btn-primary w-full" disabled={pending}>
			{pending ? 'Signing in…' : 'Sign in'}
		</button>
	</form>
</div>

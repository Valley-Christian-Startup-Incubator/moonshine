<script lang="ts">
	import { enhance } from '$app/forms';
	import type { ActionData } from './$types';

	let { form }: { form: ActionData } = $props();
</script>

<svelte:head>
	<title>Sign in — Distill Scheduler</title>
</svelte:head>

<div class="mx-auto mt-16 max-w-sm">
	<h1 class="text-xl font-semibold text-zinc-100">Distill Scheduler</h1>
	<p class="mt-2 text-sm text-zinc-400">
		Enter the shared password from your instructor. You do not need an individual account.
	</p>
	{#if form?.error}
		<div class="mt-4 rounded-md border border-red-800 bg-red-950 px-4 py-3 text-sm text-red-300">
			{form.error}
		</div>
	{/if}
	<form method="POST" use:enhance class="mt-5 space-y-4">
		<div>
			<label class="label" for="password">Password</label>
			<input id="password" name="password" type="password" class="input" autocomplete="current-password" required />
		</div>
		<button type="submit" class="btn-primary w-full">Sign in</button>
	</form>
</div>

<script lang="ts">
	import { enhance } from '$app/forms';
	import { page } from '$app/stores';
	import type { ActionData, PageData } from './$types';
	let { form, data }: { form: ActionData; data: PageData } = $props();
	let registering = $state(false);
	let pending = $state(false);
</script>

<svelte:head><title>Sign in — Moonshine</title></svelte:head>
<div class="mx-auto mt-10 max-w-md rounded-2xl border border-border bg-bg-subtle p-8">
	<p class="text-xs uppercase tracking-[0.25em] text-emerald-400">Your experiments, together</p>
	<h1 class="mt-4 text-3xl font-semibold text-zinc-100">{registering ? 'Make yourself at home.' : 'Welcome back.'}</h1>
	<p class="mt-3 text-sm leading-6 text-zinc-400">Your account keeps your runs and trained adapters easy to find. Everyone shares the classroom queue.</p>
	<div class="mt-6 flex rounded-lg bg-black/30 p-1">
		<button class="flex-1 rounded-md py-2 text-sm {registering ? 'text-zinc-400' : 'bg-bg-elevated text-white'}" onclick={() => registering = false}>Sign in</button>
		<button class="flex-1 rounded-md py-2 text-sm {registering ? 'bg-bg-elevated text-white' : 'text-zinc-400'}" onclick={() => registering = true}>Create account</button>
	</div>
	{#if form?.error}<p role="alert" class="mt-4 rounded-md bg-red-950 px-4 py-3 text-sm text-red-300">{form.error}</p>{/if}
	<form method="POST" action={registering ? '?/register' : '?/login'} use:enhance={() => {
		pending = true;
		return async ({ update }) => { try { await update(); } finally { pending = false; } };
	}} class="mt-5 space-y-4">
		<input type="hidden" name="next" value={$page.url.searchParams.get('next') ?? '/'} />
		<div><label class="label" for="username">Username</label><input id="username" name="username" class="input" autocomplete="username" minlength="3" maxlength="32" pattern="[A-Za-z0-9_-]+" required /></div>
		<div><label class="label" for="password">Password</label><input id="password" name="password" type="password" class="input" autocomplete={registering ? 'new-password' : 'current-password'} minlength={registering ? 10 : undefined} maxlength="256" required />
		{#if registering}<p class="mt-1 text-xs text-zinc-500">Use at least 10 characters.</p>{/if}</div>
		{#if registering && data.inviteRequired}<div><label class="label" for="inviteCode">Classroom invite code</label><input id="inviteCode" name="inviteCode" type="password" class="input" required /><p class="mt-1 text-xs text-zinc-500">Use the shared password from your instructor.</p></div>{/if}
		<button type="submit" class="btn-primary w-full" disabled={pending}>{pending ? 'Please wait…' : registering ? 'Create account' : 'Sign in'}</button>
	</form>
</div>

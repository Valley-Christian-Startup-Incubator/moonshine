<script lang="ts">
	import { onMount, tick } from 'svelte';
	import { enhance } from '$app/forms';
	import { completeLogin, getSession, logout, openaiAuthHeaders, startLogin } from '@openai-oauth/web';
	import type { Team } from '$lib/types';

	type Message = { role: 'user' | 'assistant'; content: string };
	type Teacher = { label: string; available: boolean };
	type TeacherField = { name: string; label: string; default: string | number; step?: number; help: string };
	let { teams, teacher, teacherFields, formError }: { teams: readonly Team[]; teacher: Teacher; teacherFields: TeacherField[]; formError?: string } = $props();
	let messages = $state<Message[]>([]);
	let generationPrompt = $state('');
	let count = $state(8);
	let selectedTeam = $state<Team>('FRC Robotics');
	let input = $state('');
	let busy = $state(false);
	let submitting = $state(false);
	let error = $state('');
	let transcript = $state<HTMLDivElement>();
	let signedIn = $state(false);
	let authBusy = $state(false);
	let extensionUrl = $state('');
	const validCount = $derived(Number.isInteger(Number(count)) && Number(count) >= 1 && Number(count) <= 20);

	onMount(async () => {
		try {
			const saved = JSON.parse(sessionStorage.getItem('moonshine-prompt-planning') ?? 'null');
			if (saved && Array.isArray(saved.messages)) {
				messages = saved.messages;
				generationPrompt = typeof saved.generationPrompt === 'string' ? saved.generationPrompt : '';
				count = Number.isInteger(saved.count) && saved.count >= 1 && saved.count <= 20 ? saved.count : 8;
				selectedTeam = teams.includes(saved.selectedTeam) ? saved.selectedTeam : 'FRC Robotics';
			}
		} catch { /* Start a new draft if saved state is invalid. */ }
		try {
			await completeLogin();
			signedIn = Boolean(await getSession());
		} catch (cause) {
			error = cause instanceof Error ? cause.message : 'Could not finish ChatGPT sign-in.';
		}
	});

	async function signIn() {
		authBusy = true;
		error = '';
		try {
			const result = await startLogin();
			if (result.status === 'needs-extension') extensionUrl = result.installUrl;
		} catch (cause) {
			error = cause instanceof Error ? cause.message : 'Could not start ChatGPT sign-in.';
		} finally {
			authBusy = false;
		}
	}

	async function signOut() {
		await logout();
		signedIn = false;
		extensionUrl = '';
	}

	function save() {
		sessionStorage.setItem('moonshine-prompt-planning', JSON.stringify({ messages, generationPrompt, count, selectedTeam }));
	}

	async function authHeaders() {
		return signedIn
			? await openaiAuthHeaders({ headers: { 'Content-Type': 'application/json' } })
			: { 'Content-Type': 'application/json' };
	}

	async function send(text = input) {
		const content = text.trim();
		if (!content || busy) return;
		const nextMessages = [...messages, { role: 'user' as const, content }];
		messages = nextMessages;
		input = '';
		busy = true;
		error = '';
		save();
		await tick();
		transcript?.scrollTo({ top: transcript.scrollHeight });
		try {
			const response = await fetch('/api/topic-chat', {
				method: 'POST',
				headers: await authHeaders(),
				body: JSON.stringify({ messages: nextMessages.slice(-20), generationPrompt })
			});
			const result = await response.json();
			if (!response.ok) throw new Error(result.error ?? 'Could not reach the chat model.');
			messages = [...messages, { role: 'assistant', content: result.message }];
			generationPrompt = result.generationPrompt;
			save();
			await tick();
			transcript?.scrollTo({ top: transcript.scrollHeight, behavior: 'smooth' });
		} catch (cause) {
			error = cause instanceof Error ? cause.message : 'Could not reach the chat model.';
		} finally {
			busy = false;
		}
	}

	function downloadPrompt() {
		const url = URL.createObjectURL(new Blob([generationPrompt.trim() + '\n'], { type: 'text/plain' }));
		const link = document.createElement('a');
		link.href = url;
		link.download = 'qa-generation-prompt.txt';
		link.click();
		setTimeout(() => URL.revokeObjectURL(url), 1000);
	}
</script>

<section aria-label="Plan training examples" class="mt-6">
	<p class="text-sm leading-6 text-zinc-400">The assistant will interview you about what the trained model should know and how it should respond. It turns your decisions into a prompt that generates Q&A pairs, then helps you revise it after you review the results.</p>
	<div class="mt-4 flex flex-wrap items-center gap-4 rounded-lg border border-border-subtle p-4">
		{#if signedIn}
			<div class="flex items-center gap-3">
				<span class="flex h-10 w-10 items-center justify-center rounded-full bg-white"><img src="/openai-blossom.svg" alt="" class="h-[22px] w-[22px]" /></span>
				<div><p class="text-sm font-medium text-zinc-100">ChatGPT connected</p><p class="text-xs text-zinc-500">Using your ChatGPT connection</p></div>
			</div>
			<button type="button" class="text-xs text-zinc-400 underline underline-offset-2 hover:text-zinc-100" onclick={signOut}>Disconnect</button>
		{:else}
			<div class="flex flex-col items-center gap-1.5">
				<button type="button" class="chatgpt-signin" disabled={authBusy} onclick={signIn}><img src="/openai-blossom.svg" alt="" class="h-[22px] w-[22px] shrink-0" />{authBusy ? 'Connecting…' : 'Sign in with ChatGPT'}</button>
				<a href="https://github.com/EvanZhouDev/openai-oauth" target="_blank" rel="noopener noreferrer" class="text-[10px] leading-tight text-zinc-500 underline underline-offset-2 hover:text-zinc-300">Powered by OpenAI OAuth</a>
			</div>
			<span class="text-xs leading-5 text-zinc-500">Optional. This connection requires the independent OpenAI OAuth extension. Install it for <a href="https://chromewebstore.google.com/detail/sign-in-with-chatgpt/odbgboachaefbbbdiffcefhpkekhfcna" target="_blank" rel="noopener noreferrer" class="text-blue-300 underline underline-offset-2 hover:text-blue-200">Chrome</a> or <a href="https://addons.mozilla.org/firefox/addon/sign-in-with-chatgpt/" target="_blank" rel="noopener noreferrer" class="text-blue-300 underline underline-offset-2 hover:text-blue-200">Firefox</a>. The server's configured model works without it.</span>
		{/if}
		{#if extensionUrl}<p class="w-full text-xs leading-5 text-zinc-400">Install the <a href={extensionUrl} target="_blank" rel="noopener noreferrer" class="text-blue-300 underline">Sign in with ChatGPT browser extension</a>, then press Sign in again.</p>{/if}
	</div>
	<div class="mt-5 rounded-lg border border-border-subtle bg-bg-subtle">
		<div class="border-b border-border-subtle px-4 py-3 text-xs font-medium uppercase tracking-wide text-zinc-400">Dataset interview</div>
		<div bind:this={transcript} class="max-h-80 min-h-52 space-y-4 overflow-y-auto p-4" role="log" aria-live="polite">
			{#if messages.length === 0}
				<div class="text-sm">
					<p class="mb-1 text-xs font-medium text-zinc-400">Assistant</p>
					<p class="rounded-lg bg-zinc-800 px-3 py-2 leading-6 text-zinc-100">What should the trained model help someone do? Describe one real situation where a user would ask it for help.</p>
				</div>
			{:else}
				{#each messages as message}
					<div class:ml-10={message.role === 'user'} class="text-sm">
						<p class="mb-1 text-xs font-medium {message.role === 'user' ? 'text-blue-300' : 'text-zinc-400'}">{message.role === 'user' ? 'You' : 'Assistant'}</p>
						<p class="whitespace-pre-wrap rounded-lg px-3 py-2 leading-6 {message.role === 'user' ? 'bg-blue-950 text-blue-50' : 'bg-zinc-800 text-zinc-100'}">{message.content}</p>
					</div>
			{/each}
			{/if}
			{#if busy}<p class="text-sm text-zinc-400">Revising the generation prompt…</p>{/if}
		</div>
		<div class="border-t border-border-subtle p-3">
			<label for="topic-message" class="sr-only">Message the planning assistant</label>
			<textarea id="topic-message" bind:value={input} rows="3" maxlength="4000" class="input resize-y" placeholder="For example: A new FRC member is choosing a gear ratio for an intake and needs to understand the speed and torque tradeoff." onkeydown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); send(); } }}></textarea>
			<div class="mt-2 flex items-center justify-between gap-3">
				<span class="text-xs text-zinc-500">Enter to send · Shift+Enter for a new line</span>
				<button type="button" class="btn-primary" disabled={busy || !input.trim()} onclick={() => send()}>Send</button>
			</div>
			{#if error}<p role="alert" class="mt-2 text-sm text-red-300">{error}</p>{/if}
		</div>
	</div>

	<form class="mt-6 border-t border-border-subtle pt-5" method="POST" use:enhance={() => {
		save();
		submitting = true;
		return async ({ update }) => {
			try { await update({ reset: false }); }
			finally { submitting = false; }
		};
	}}>
		<input type="hidden" name="jobType" value="teacher-gen" />
		<h3 class="text-base font-semibold text-zinc-100">Q&A generation prompt</h3>
		<p class="mt-2 text-sm leading-6 text-zinc-400">This prompt is sent to the configured teacher model. The teacher uses it to create both the questions and the answers your student will learn from.</p>
		<label for="generation-prompt" class="sr-only">Q&A generation prompt</label>
		<textarea id="generation-prompt" name="generationPrompt" bind:value={generationPrompt} rows="9" maxlength="6000" required class="input mt-3 resize-y" placeholder="The assistant will draft a prompt here. You can also write one yourself." onblur={save}></textarea>
		<div class="mt-4 flex flex-wrap items-end gap-3">
			<div><label for="example-count" class="label">Questions</label><input id="example-count" name="questionCount" type="number" min="1" max="20" bind:value={count} class="input w-24" onblur={save} /></div>
			<button type="button" class="btn-secondary" disabled={!generationPrompt.trim()} onclick={downloadPrompt}>Download prompt</button>
		</div>

		<section class="mt-5 rounded-lg border border-border-subtle p-4" aria-label="Teacher configuration">
			<div class="flex flex-wrap items-center justify-between gap-3">
				<div>
					<p class="text-xs text-zinc-400">Teacher</p>
					<p class="text-sm font-medium text-zinc-100">{teacher.label} <span class="ml-2 text-xs font-normal text-zinc-500">{teacher.available ? 'Source configured' : 'Setup needed'}</span></p>
				</div>
				<a href="/admin#teacher-model" class="btn-secondary text-xs">Configure teacher</a>
			</div>
			<div class="mt-4 grid gap-4 border-t border-border-subtle pt-3 sm:grid-cols-2">
				{#each teacherFields as field}
					<div>
						<label for={'plan-' + field.name} class="text-xs text-zinc-400">{field.label}</label>
						<input id={'plan-' + field.name} name={field.name} type="number" value={field.default} min={field.name === 'MAX_TOKENS' ? 1 : 0} step={field.step ?? 1} class="input" />
						<p class="mt-1 text-xs leading-5 text-zinc-500">{field.help}</p>
					</div>
				{/each}
			</div>
		</section>

		{#if formError}<p role="alert" class="mt-4 rounded-md border border-red-900 bg-red-950 p-3 text-sm text-red-200">{formError}</p>{/if}
		<div class="mt-6 flex flex-col items-stretch gap-3 border-t border-border-subtle pt-5 sm:flex-row sm:items-end">
			<div class="flex-1">
				<label class="label" for="plan-team">Your team</label>
				<select id="plan-team" name="team" bind:value={selectedTeam} class="input" onchange={save}>{#each teams as team}<option value={team}>{team}</option>{/each}</select>
			</div>
			<button type="submit" class="btn-primary min-h-10" disabled={submitting || !teacher.available || !generationPrompt.trim() || !validCount}>
				{#if submitting}<span class="generating-dot" aria-hidden="true"></span> Sending to teacher…{:else}Generate questions →{/if}
			</button>
		</div>
		<p class="mt-3 text-xs leading-5 text-zinc-500">The job opens with a live generation animation. When it finishes, download and review the teacher's Q&A before splitting it for training.</p>
	</form>
</section>

<style>
	.chatgpt-signin {
		display: inline-flex;
		min-height: 52px;
		min-width: 224px;
		align-items: center;
		justify-content: center;
		gap: 12px;
		border: 1px solid #d9d9d9;
		border-radius: 9999px;
		background: #fff;
		padding: 14px 22px;
		color: #111;
		font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
		font-size: 15px;
		font-weight: 450;
		line-height: 1.2;
		white-space: nowrap;
		cursor: pointer;
		transition: background 150ms ease;
	}
	.chatgpt-signin:hover:not(:disabled) { background: #f5f5f5; }
	.chatgpt-signin:focus-visible { outline: 2px solid #a8d1ff; outline-offset: 3px; }
	.chatgpt-signin:disabled { cursor: wait; opacity: .6; }
	.generating-dot { display: inline-block; width: .55rem; height: .55rem; margin-right: .5rem; border-radius: 9999px; background: currentColor; animation: pulse 1s ease-in-out infinite; }
	@keyframes pulse { 0%, 100% { opacity: .35; transform: scale(.8); } 50% { opacity: 1; transform: scale(1.15); } }
	@media (prefers-reduced-motion: reduce) { .generating-dot { animation: none; } }
</style>

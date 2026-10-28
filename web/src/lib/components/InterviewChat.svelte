<script lang="ts">
	import { onMount, tick } from 'svelte';
	import { z } from 'zod';
	import InterviewPrompt from './InterviewPrompt.svelte';
	import { loadInterviewProgress, saveInterviewProgress, type InterviewMessage as Msg } from '$lib/interview-progress';

	let {
		teacher,
		ownerName,
		onPrompt
	}: { teacher: string; ownerName: string; onPrompt: (prompt: string) => void } = $props();

	const replySchema = z.object({ reply: z.string(), prompt: z.string().nullable() });
	let messages = $state<Msg[]>([]);
	let input = $state('');
	let busy = $state(false);
	let error = $state('');
	let storageError = $state('');
	let prompt = $state('');
	let restored = $state(false);
	const request = new AbortController();
	let scroller: HTMLDivElement | undefined = $state();

	const PROMPT_BLOCK = /=== PROMPT[\s\S]*?(=== END PROMPT ===|$)/;

	function display(m: Msg): string {
		if (m.role === 'assistant' && PROMPT_BLOCK.test(m.content)) {
			const rest = m.content.replace(PROMPT_BLOCK, '').trim();
			return (rest ? rest + '\n\n' : '') + 'Your finished prompt is ready below the chat.';
		}
		return m.content;
	}

	function save() {
		try {
			saveInterviewProgress(ownerName, { messages: $state.snapshot(messages), input, prompt });
			storageError = '';
		} catch {
			storageError = 'Could not save progress in this browser. Download your transcript before leaving.';
		}
	}

	async function scrollDown() {
		await tick();
		scroller?.scrollTo({ top: scroller.scrollHeight, behavior: 'smooth' });
	}

	async function ask(history: Msg[]) {
		busy = true;
		error = '';
		try {
			const res = await fetch('/api/interview', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ messages: history }),
				signal: request.signal
			});
			if (!res.ok) throw new Error('The teacher did not answer. Try again or ask an instructor to check Ollama.');
			const data = replySchema.parse(await res.json());
			messages.push({ role: 'assistant', content: data.reply });
			if (data.prompt) prompt = data.prompt;
			save();
			if (data.prompt) onPrompt(data.prompt);
		} catch (e) {
			if (!request.signal.aborted) error = e instanceof Error ? e.message : String(e);
		} finally {
			busy = false;
			scrollDown();
		}
	}

	async function send() {
		const text = input.trim();
		if (!text || busy) return;
		messages.push({ role: 'user', content: text });
		input = '';
		save();
		scrollDown();
		await ask($state.snapshot(messages));
	}

	function retry() {
		if (busy) return;
		if (messages.at(-1)?.role === 'user') ask($state.snapshot(messages));
		else if (messages.length === 0) ask([]);
	}

	function restart() {
		if ((messages.length || input || prompt) && !confirm('Start over? This clears your saved interview, draft reply, and finished prompt. Download your transcript first if you want to keep it.')) return;
		messages = [];
		input = '';
		prompt = '';
		restored = false;
		onPrompt('');
		save();
		ask([]);
	}

	const transcript = $derived(
		messages.map((m) => `## ${m.role === 'user' ? 'You' : 'Teacher'}\n\n${m.content}`).join('\n\n')
			+ (input ? `\n\n## Unsent reply\n\n${input}` : '')
	);

	function downloadTranscript() {
		const text = transcript;
		const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown;charset=utf-8' }));
		const link = document.createElement('a');
		link.href = url;
		link.download = 'moonshine-interview.md';
		link.click();
		setTimeout(() => URL.revokeObjectURL(url), 1000);
	}

	onMount(() => {
		try {
			const saved = loadInterviewProgress(ownerName);
			messages = saved.messages;
			input = saved.input;
			prompt = saved.prompt;
			restored = messages.length > 0 || Boolean(input);
			onPrompt(prompt);
			save();
		} catch {
			storageError = 'Could not restore saved progress. Download your transcript before leaving.';
		}
		if (messages.length === 0) ask([]);
		else {
			if (messages.at(-1)?.role === 'user') error = 'Your last reply was saved, but the teacher’s response did not finish. Try again to continue.';
			scrollDown();
		}
		return () => request.abort();
	});

	function onKey(e: KeyboardEvent) {
		if (e.key === 'Enter' && !e.shiftKey) {
			e.preventDefault();
			send();
		}
	}
</script>

<div class="card flex flex-col">
	<p class="border-b border-border-subtle px-4 py-2 text-xs text-zinc-400" role="status">
		{#if storageError}<span class="text-amber-300">{storageError}</span>
		{:else}{restored ? 'Saved interview restored. ' : ''}Chat and drafts save in this browser as {ownerName}. Close this tab and return here to continue.{/if}
	</p>
	<div class="flex flex-wrap items-center justify-between gap-2 border-b border-border-subtle px-4 py-2 text-xs text-zinc-500">
		<span>Interview with the teacher model <span class="font-mono text-zinc-300">{teacher}</span></span>
		<div class="flex shrink-0 gap-3">
			<button type="button" class="hover:text-zinc-200" onclick={downloadTranscript} disabled={!messages.length && !input}>Download transcript</button>
			<button type="button" class="hover:text-zinc-200" onclick={restart} disabled={busy}>Start over</button>
		</div>
	</div>
	<div bind:this={scroller} class="max-h-[480px] min-h-[260px] space-y-3 overflow-y-auto p-4" aria-live="polite">
		{#each messages as m, i (i)}
			<div class="flex {m.role === 'user' ? 'justify-end' : 'justify-start'}">
				<div
					class="max-w-[85%] whitespace-pre-wrap rounded-lg px-3 py-2 text-sm leading-6
						{m.role === 'user' ? 'bg-emerald-900/50 text-emerald-50' : 'bg-bg-subtle text-zinc-200'}"
				>
					{display(m)}
				</div>
			</div>
		{/each}
		{#if busy}
			<p class="text-sm text-zinc-500">The teacher is thinking… (this can take up to a minute)</p>
		{/if}
		{#if error}
			<div class="rounded-md bg-red-950 px-3 py-2 text-sm text-red-300">
				{error}
				<button type="button" class="ml-2 underline" onclick={retry}>Try again</button>
			</div>
		{/if}
	</div>
	<div class="flex gap-2 border-t border-border-subtle p-3">
		<textarea
			class="input min-h-[44px] resize-y"
			rows="2"
			placeholder="Type your answer… (Enter to send, Shift+Enter for a new line)"
			bind:value={input}
			onkeydown={onKey}
			oninput={(event) => { input = event.currentTarget.value; save(); }}
			disabled={busy}
			aria-label="Your answer"
		></textarea>
		<button type="button" class="btn-primary shrink-0 self-end" onclick={send} disabled={busy || !input.trim()}>
			Send
		</button>
	</div>
</div>

<details class="mt-3 rounded-md border border-border-subtle p-3">
	<summary class="cursor-pointer text-sm text-zinc-300">Continue in Claude or another AI</summary>
	<div class="mt-3"><InterviewPrompt local {transcript} /></div>
</details>

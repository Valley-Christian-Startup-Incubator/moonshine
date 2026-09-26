<script lang="ts">
	import { onMount, tick } from 'svelte';

	type Msg = { role: 'user' | 'assistant'; content: string };
	let {
		teacher,
		onPrompt
	}: { teacher: string; onPrompt: (prompt: string) => void } = $props();

	const STORE = 'moonshine.interview.v1';
	let messages = $state<Msg[]>([]);
	let input = $state('');
	let busy = $state(false);
	let error = $state('');
	let scroller: HTMLDivElement | undefined = $state();

	const PROMPT_BLOCK = /=== PROMPT[\s\S]*?(=== END PROMPT ===|$)/;

	function display(m: Msg): string {
		if (m.role === 'assistant' && PROMPT_BLOCK.test(m.content)) {
			const rest = m.content.replace(PROMPT_BLOCK, '').trim();
			return (rest ? rest + '\n\n' : '') + '✅ Your PROMPT is ready. It is in the PROMPT box below.';
		}
		return m.content;
	}

	function save() {
		try {
			sessionStorage.setItem(STORE, JSON.stringify(messages));
		} catch {
			/* ignore */
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
				body: JSON.stringify({ messages: history })
			});
			const data = await res.json();
			if (!res.ok) throw new Error(data.error ?? 'The teacher did not answer.');
			messages.push({ role: 'assistant', content: data.reply });
			if (data.prompt) onPrompt(data.prompt);
			save();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
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
		if (messages.at(-1)?.role === 'user') ask($state.snapshot(messages));
		else if (messages.length === 0) ask([]);
	}

	function restart() {
		if (messages.length > 1 && !confirm('Start a new interview? The current conversation will be cleared.')) return;
		messages = [];
		save();
		ask([]);
	}

	onMount(() => {
		try {
			const saved = JSON.parse(sessionStorage.getItem(STORE) ?? '[]');
			if (Array.isArray(saved)) messages = saved;
		} catch {
			/* ignore */
		}
		if (messages.length === 0) ask([]);
		else scrollDown();
	});

	function onKey(e: KeyboardEvent) {
		if (e.key === 'Enter' && !e.shiftKey) {
			e.preventDefault();
			send();
		}
	}
</script>

<div class="card flex flex-col">
	<div class="flex items-center justify-between border-b border-border-subtle px-4 py-2 text-xs text-zinc-500">
		<span>Interview with the teacher model <span class="font-mono text-zinc-300">{teacher}</span></span>
		<button type="button" class="hover:text-zinc-200" onclick={restart} disabled={busy}>Start over</button>
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
			disabled={busy}
			aria-label="Your answer"
		></textarea>
		<button type="button" class="btn-primary shrink-0 self-end" onclick={send} disabled={busy || !input.trim()}>
			Send
		</button>
	</div>
</div>

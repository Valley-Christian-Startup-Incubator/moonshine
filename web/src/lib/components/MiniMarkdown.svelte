<script lang="ts">
	/** Small, safe Markdown subset (headings, bullets, numbered lists, **bold**, `code`) without {@html}. */
	let { text }: { text: string } = $props();

	type Block = { kind: 'h' | 'ul' | 'ol' | 'p'; lines: string[] };
	const blocks = $derived.by(() => {
		const out: Block[] = [];
		for (const raw of (text ?? '').replace(/\r/g, '').split('\n')) {
			const line = raw.trimEnd();
			if (!line.trim()) {
				out.push({ kind: 'p', lines: [] });
				continue;
			}
			let m: RegExpExecArray | null;
			const last = out[out.length - 1];
			if ((m = /^\s*#{1,6}\s+(.*)$/.exec(line))) out.push({ kind: 'h', lines: [m[1]] });
			else if ((m = /^\s*[-*•]\s+(.*)$/.exec(line))) {
				if (last?.kind === 'ul') last.lines.push(m[1]);
				else out.push({ kind: 'ul', lines: [m[1]] });
			} else if ((m = /^\s*\d+[.)]\s+(.*)$/.exec(line))) {
				if (last?.kind === 'ol') last.lines.push(m[1]);
				else out.push({ kind: 'ol', lines: [m[1]] });
			} else if (last && (last.kind === 'ul' || last.kind === 'ol') && /^\s{2,}/.test(raw))
				last.lines[last.lines.length - 1] += ' ' + line.trim();
			else if (last?.kind === 'p' && last.lines.length) last.lines.push(line.trim());
			else out.push({ kind: 'p', lines: [line.trim()] });
		}
		return out.filter((b) => b.lines.length);
	});
	const parts = (s: string) =>
		s
			// inline math like $\cot(x)$ -> cot(x); *emphasis* -> plain text
			.replace(/\$([^$\n]+)\$/g, (_, m: string) => '`' + m.replace(/\\([a-zA-Z]+)/g, '$1') + '`')
			.replace(/(^|[^*])\*([^*\s][^*]*?)\*(?!\*)/g, '$1$2')
			.split(/(\*\*[^*]+\*\*|`[^`]+`)/g)
			.filter(Boolean)
			.map((t) =>
				t.startsWith('**') && t.endsWith('**') && t.length > 4
					? { b: true, c: false, t: t.slice(2, -2) }
					: t.startsWith('`') && t.endsWith('`') && t.length > 2
						? { b: false, c: true, t: t.slice(1, -1) }
						: { b: false, c: false, t }
			);
</script>

{#snippet inline(s: string)}{#each parts(s) as p, i (i)}{#if p.b}<strong class="font-semibold text-zinc-100">{p.t}</strong>{:else if p.c}<code class="font-mono text-zinc-200">{p.t}</code>{:else}{p.t}{/if}{/each}{/snippet}

<div class="space-y-2 text-sm leading-6 text-zinc-300">
	{#each blocks as b, i (i)}
		{#if b.kind === 'h'}
			<p class="pt-1 font-semibold text-zinc-100">{@render inline(b.lines[0])}</p>
		{:else if b.kind === 'ul'}
			<ul class="list-disc space-y-1 pl-5">{#each b.lines as l, j (j)}<li>{@render inline(l)}</li>{/each}</ul>
		{:else if b.kind === 'ol'}
			<ol class="list-decimal space-y-1 pl-5">{#each b.lines as l, j (j)}<li>{@render inline(l)}</li>{/each}</ol>
		{:else}
			<p>{@render inline(b.lines.join(' '))}</p>
		{/if}
	{/each}
</div>

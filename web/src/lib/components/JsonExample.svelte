<script lang="ts">
	let { code }: { code: string } = $props();
	const tokens = $derived(
		code.match(/"(?:\\.|[^"\\])*"\s*(?=:)|"(?:\\.|[^"\\])*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\b(?:true|false|null)\b|[^"\d\w-]+|./g) ?? []
	);
</script>

<pre class="overflow-x-auto rounded-lg border border-border-subtle bg-zinc-950 p-3 text-xs text-zinc-300"><code>{#each tokens as token, i}<span class:key={token.startsWith('"') && tokens[i + 1]?.startsWith(':')} class:string={token.startsWith('"') && !tokens[i + 1]?.startsWith(':')} class:literal={/^(?:-?\d|true|false|null)/.test(token)}>{token}</span>{/each}</code></pre>

<style>
	.key { color: #7dd3fc; }
	.string { color: #86efac; }
	.literal { color: #fcd34d; }
</style>

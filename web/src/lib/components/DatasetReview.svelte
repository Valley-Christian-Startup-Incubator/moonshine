<script lang="ts">
	let { file = null, split = false }: { file?: File | null; split?: boolean } =
		$props();
	let localFile = $state<File | null>(null);
	let rows = $state<Record<string, unknown>[]>([]);
	let index = $state(0);
	let error = $state('');
	let loading = $state(false);
	let percentage = $state(80);
	let frozen = $state<{
		train: string;
		validation: string;
		manifest: string;
		trainCount: number;
		validationCount: number;
	} | null>(null);
	const activeFile = $derived(split ? localFile : file);
	const sample = $derived(rows[index]);
	const SEED = 42;

	$effect(() => {
		const input = activeFile;
		let cancelled = false;
		rows = [];
		index = 0;
		error = '';
		frozen = null;
		loading = !!input;
		if (input) {
			(async () => {
				try {
					if (input.size > 20 * 1024 * 1024)
						throw new Error(
							'Browser review and splitting support files up to 20 MB. Use a smaller file for this step.'
						);
					const parsed = (await input.text())
						.split(/\r?\n/)
						.flatMap((line, lineIndex) => {
							if (!line.trim()) return [];
							let row;
							try {
								row = JSON.parse(line);
							} catch {
								throw new Error(`Line ${lineIndex + 1} is not valid JSON.`);
							}
							if (!row || typeof row !== 'object' || Array.isArray(row))
								throw new Error(`Line ${lineIndex + 1} must be a JSON object.`);
							if (
								split &&
								(typeof row.prompt !== 'string' ||
									!row.prompt.trim() ||
									typeof row.completion !== 'string' ||
									!row.completion.trim())
							)
								throw new Error(
									`Line ${lineIndex + 1} needs nonempty prompt and completion fields.`
								);
							return [row as Record<string, unknown>];
						});
					if (!parsed.length) throw new Error('This file has no examples.');
					if (!cancelled) rows = parsed;
				} catch (reason) {
					if (!cancelled)
						error =
							reason instanceof Error
								? reason.message
								: 'Could not read this file.';
				} finally {
					if (!cancelled) loading = false;
				}
			})();
		}
		return () => {
			cancelled = true;
		};
	});

	function download(
		name: string,
		content: BlobPart,
		type = 'application/x-ndjson'
	) {
		const url = URL.createObjectURL(new Blob([content], { type }));
		const link = document.createElement('a');
		link.href = url;
		link.download = name;
		link.click();
		setTimeout(() => URL.revokeObjectURL(url), 1000);
	}
	function freezeSplit() {
		if (frozen) return;
		// Keep repeated questions together so alternate answers cannot cross the boundary.
		const groups = new Map<string, Record<string, unknown>[]>();
		for (const row of rows) {
			const key = (row.prompt as string)
				.normalize('NFKC')
				.trim()
				.replace(/\s+/g, ' ')
				.toLowerCase();
			const group = groups.get(key);
			if (group) group.push(row);
			else groups.set(key, [row]);
		}
		if (groups.size < 5) {
			error =
				'Use at least 5 distinct questions so both splits have enough examples.';
			return;
		}
		// Stable key order + seeded Fisher–Yates makes the partition reproducible even if rows move.
		const keys = [...groups.keys()].sort();
		let state = SEED;
		for (let i = keys.length - 1; i > 0; i--) {
			state = (Math.imul(1664525, state) + 1013904223) >>> 0;
			const j = Math.floor((state / 4294967296) * (i + 1));
			[keys[i], keys[j]] = [keys[j], keys[i]];
		}
		const count = Math.max(
			1,
			Math.round((keys.length * (100 - percentage)) / 100)
		);
		const validationKeys = new Set(keys.slice(0, count));
		const train = keys
			.filter((key) => !validationKeys.has(key))
			.flatMap((key) => groups.get(key)!);
		const validation = keys
			.filter((key) => validationKeys.has(key))
			.flatMap((key) => groups.get(key)!);
		const jsonl = (items: Record<string, unknown>[]) =>
			items.map((row) => JSON.stringify(row)).join('\n') + '\n';
		frozen = {
			train: jsonl(train),
			validation: jsonl(validation),
			trainCount: train.length,
			validationCount: validation.length,
			manifest: JSON.stringify(
				{
					version: 1,
					seed: SEED,
					algorithm: 'sorted-normalized-prompt-groups-lcg-fisher-yates-v1',
					requestedTrainingPercent: percentage,
					source: activeFile?.name,
					trainingRows: train.length,
					validationRows: validation.length,
					validationPrompts: [...validationKeys]
				},
				null,
				2
			)
		};
		error = '';
	}
</script>

{#if split}
	<div class="mt-5">
		<label for="split-file" class="label">Teacher Q&A file</label>
		<div id="split-file-help" class="mb-3 space-y-2 text-sm text-zinc-400">
			<p>Upload the output downloaded from a completed <strong>Teacher answers</strong>
				job. This step separates those examples into training and validation files.</p>
			<p>Use a plain-text <code>.jsonl</code> file with one JSON object per line.
				Each needs nonempty <code>"prompt"</code> (question) and
				<code>"completion"</code> (teacher answer) strings:</p>
			<pre class="overflow-x-auto rounded-lg border border-border-subtle bg-zinc-950 p-3 text-xs text-zinc-300"><code>{'{"prompt":"What is torque?","completion":"Torque is a turning force."}'}</code></pre>
			<p>Use double quotes, with no commas between lines or surrounding square brackets.
				Write line breaks inside an answer as <code>\n</code>. Include at least two
				different questions so both splits can contain examples.</p>
			<a href="/examples/training.jsonl" class="text-xs underline">Download Q&A example</a>
		</div>
		<input
			id="split-file"
			aria-describedby="split-file-help"
			type="file"
			accept=".jsonl"
			disabled={!!frozen}
			class="input"
			onchange={(event) => (localFile = event.currentTarget.files?.[0] ?? null)}
		/>
		<p class="mt-2 text-xs leading-5 text-zinc-500">Local file · max 20 MB</p>
	</div>
{/if}
{#if loading}<p class="mt-3 text-sm text-zinc-400" role="status">
		Reading examples…
	</p>{/if}
{#if error}<p class="mt-3 text-sm text-amber-200" role="alert">{error}</p>{/if}
{#if sample}
	<details class="mt-4">
		<summary class="cursor-pointer text-sm text-zinc-400"
			>Review {rows.length} examples</summary
		>
		<section
			class="mt-4 overflow-hidden rounded-lg border border-border"
			aria-label="Sample browser"
		>
			<div
				class="flex flex-wrap items-center justify-between gap-2 border-b border-border-subtle bg-bg-subtle px-4 py-3"
			>
				<span class="text-xs text-zinc-400" aria-live="polite"
					>Sample {index + 1} of {rows.length}</span
				>
				<div class="flex gap-3">
					<button
						type="button"
						class="text-xs text-zinc-300 disabled:opacity-30"
						disabled={index === 0}
						onclick={() => index--}>Previous</button
					><button
						type="button"
						class="text-xs text-blue-300 disabled:opacity-30"
						disabled={index === rows.length - 1}
						onclick={() => index++}>Next sample</button
					>
				</div>
			</div>
			<div class="max-h-72 space-y-4 overflow-y-auto p-4">
				{#each Object.entries(sample) as [key, value]}<div>
						<p class="text-xs font-medium text-zinc-500">
							{key === 'prompt'
								? 'Question'
								: key === 'completion'
									? 'Teacher answer'
									: key}
						</p>
						<p
							class="mt-1 whitespace-pre-wrap break-words text-sm leading-6 text-zinc-200"
						>
							{typeof value === 'string' ? value : JSON.stringify(value)}
						</p>
					</div>{/each}
			</div>
		</section>
		<button
			type="button"
			class="mt-3 text-xs text-blue-300 hover:underline"
			onclick={() => activeFile && download(activeFile.name, activeFile)}
			>Save original ↓</button
		>
	</details>
{/if}
{#if split && rows.length}
	<div class="mt-5 border-t border-border-subtle pt-5">
		<label for="split-ratio" class="label">Training / validation</label><select
			id="split-ratio"
			class="input"
			bind:value={percentage}
			disabled={!!frozen}
			><option value={80}>80% training / 20% validation</option><option
				value={85}>85% training / 15% validation</option
			><option value={90}>90% training / 10% validation</option></select
		>
		{#if frozen}
			<div
				class="mt-4 rounded-lg border border-emerald-900 bg-emerald-950/30 p-4"
			>
				<p class="text-sm font-medium text-emerald-200" role="status">
					Split frozen · {frozen.trainCount} training / {frozen.validationCount} validation
				</p>
				<p class="mt-2 text-xs leading-5 text-zinc-400">
					Save all three files before leaving.
				</p>
				<div class="mt-4 flex flex-wrap gap-3">
					<button
						type="button"
						class="btn-secondary"
						onclick={() => frozen && download('training.jsonl', frozen.train)}
						>Training ↓</button
					><button
						type="button"
						class="btn-secondary"
						onclick={() =>
							frozen && download('validation.jsonl', frozen.validation)}
						>Validation ↓</button
					><button
						type="button"
						class="btn-secondary"
						onclick={() =>
							frozen &&
							download(
								'split-manifest.json',
								frozen.manifest,
								'application/json'
							)}>Split record ↓</button
					>
				</div>
			</div>
		{:else}<button type="button" class="btn-primary mt-4" onclick={freezeSplit}
				>Freeze this split →</button
			>{/if}
	</div>
{/if}

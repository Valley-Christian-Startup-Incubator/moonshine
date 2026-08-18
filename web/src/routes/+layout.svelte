<script lang="ts">
	import '../app.css';
	import { page } from '$app/stores';
	import { toastState } from '$lib/toast.svelte';

	let { children } = $props();
	const toasts = toastState();

	const navItems = [
		{ href: '/', label: 'Submit' },
		{ href: '/jobs', label: 'Jobs' },
		{ href: '/admin', label: 'Admin' }
	];
</script>

<div class="min-h-screen bg-bg">
	<header class="border-b border-border-subtle bg-bg-subtle">
		<div class="mx-auto flex max-w-6xl items-center justify-between px-4 py-3 sm:px-6">
			<a href="/" class="flex items-center gap-2 font-semibold text-zinc-100">
				<span class="font-mono text-sm text-zinc-500">◆</span>
				Distill Scheduler
			</a>
			<nav class="flex gap-1">
				{#each navItems as item (item.href)}
					<a
						href={item.href}
						class="rounded-md px-3 py-1.5 text-sm font-medium transition-colors
							{$page.url.pathname === item.href
							? 'bg-bg-elevated text-zinc-100'
							: 'text-zinc-400 hover:text-zinc-200'}"
					>
						{item.label}
					</a>
				{/each}
			</nav>
		</div>
	</header>

	<main class="mx-auto max-w-6xl px-4 py-8 sm:px-6">
		{@render children()}
	</main>
</div>

<div class="fixed bottom-4 right-4 z-50 flex flex-col gap-2">
	{#each toasts as toast (toast.id)}
		<div
			class="card min-w-[260px] px-4 py-3 text-sm shadow-lg
				{toast.kind === 'success' ? 'border-emerald-800' : 'border-red-800'}"
		>
			{toast.message}
		</div>
	{/each}
</div>

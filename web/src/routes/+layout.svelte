<script lang="ts">
	import '../app.css';
	import { page } from '$app/stores';
	import { toastState } from '$lib/toast.svelte';

	let { children, data } = $props();
	const toasts = toastState();

	const navItems = [
		{ href: '/', label: 'Lab' },
		{ href: '/jobs', label: 'Jobs' }
	];
	const active = (href: string) =>
		href === '/' ? $page.url.pathname === '/' : $page.url.pathname.startsWith(href);
</script>

<div class="min-h-screen bg-bg">
	<header class="border-b border-border-subtle bg-bg-subtle">
		<div class="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6">
			<a href="/" class="flex items-center gap-2 font-semibold text-zinc-100">
				<span class="font-mono text-sm text-zinc-500">◆</span>
				Moonshine
			</a>
			{#if data.user}
				<nav class="flex flex-wrap items-center gap-1">
					{#if data.userName}
						{#each navItems as item (item.href)}
							<a
								href={item.href}
								class="rounded-md px-3 py-1.5 text-sm font-medium transition-colors
									{active(item.href) ? 'bg-bg-elevated text-zinc-100' : 'text-zinc-400 hover:text-zinc-200'}"
							>
								{item.label}
							</a>
						{/each}
						<span class="ml-2 hidden h-5 w-px bg-border sm:inline-block"></span>
						<span class="ml-2 text-sm text-zinc-300" title="Signed in as">👤 {data.userName}</span>
						<a
							href="/welcome?next={encodeURIComponent($page.url.pathname)}"
							class="rounded-md px-2 py-1.5 text-xs text-zinc-400 hover:text-zinc-200">Switch</a
						>
					{/if}
					<form method="POST" action="/logout">
						<button
							type="submit"
							class="rounded-md px-3 py-1.5 text-sm font-medium text-zinc-400 transition-colors hover:text-zinc-200"
						>
							Sign out
						</button>
					</form>
				</nav>
			{/if}
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

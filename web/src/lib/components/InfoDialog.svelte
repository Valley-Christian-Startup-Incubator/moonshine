<script lang="ts">
	import type { Snippet } from 'svelte';
	let { title, children }: { title: string; children: Snippet } = $props();
	let dialog: HTMLDialogElement;
</script>

<button
	type="button"
	class="info-button"
	aria-label={`About ${title.toLowerCase()}`}
	{title}
	onclick={() => dialog.showModal()}
>
	<svg width="16" height="16" viewBox="0 0 20 20" fill="none" aria-hidden="true"
		><circle cx="10" cy="10" r="7.5" stroke="currentColor" /><path
			d="M10 9v5"
			stroke="currentColor"
			stroke-linecap="round"
		/><circle cx="10" cy="6.5" r=".8" fill="currentColor" /></svg
	>
</button>
<dialog bind:this={dialog} aria-label={title}>
	<div class="dialog-heading">
		<h2>{title}</h2>
		<button
			type="button"
			class="close"
			aria-label="Close explanation"
			onclick={() => dialog.close()}>✕</button
		>
	</div>
	<div class="explanation">{@render children()}</div>
</dialog>

<style>
	.info-button {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 28px;
		height: 28px;
		flex-shrink: 0;
		border-radius: 50%;
		color: #71717a;
		vertical-align: middle;
	}
	.info-button:hover {
		background: #27272a;
		color: #e4e4e7;
	}
	button:focus-visible {
		outline: 2px solid #93c5fd;
		outline-offset: 2px;
	}
	dialog {
		position: fixed;
		inset: 0;
		margin: auto;
		width: min(480px, calc(100% - 32px));
		max-height: calc(100dvh - 48px);
		overflow-y: auto;
		padding: 24px;
		border: 1px solid #3f3f46;
		border-radius: 14px;
		background: #18181b;
		color: #e4e4e7;
		box-shadow: 0 24px 80px #0008;
	}
	dialog::backdrop {
		background: #0009;
		backdrop-filter: blur(3px);
	}
	.dialog-heading {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 16px;
		margin-bottom: 16px;
	}
	h2 {
		font-size: 17px;
		font-weight: 600;
	}
	.close {
		width: 30px;
		height: 30px;
		flex-shrink: 0;
		border-radius: 6px;
		color: #a1a1aa;
	}
	.close:hover {
		background: #27272a;
		color: white;
	}
	.explanation {
		font-size: 14px;
		line-height: 1.75;
		color: #a1a1aa;
	}
	.explanation :global(p + p) {
		margin-top: 12px;
	}
	.explanation :global(strong) {
		color: #e4e4e7;
		font-weight: 500;
	}
</style>

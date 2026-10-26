<script lang="ts">
	import { onMount } from 'svelte';
	import { STATUS_LABELS, type JobStatus, type JobType } from '$lib/types';

	type Progress = { done: number; percent: number | null; phaseLabel: string | null } | null;
	let {
		type,
		status,
		progress,
		docNames = []
	}: { type: JobType; status: JobStatus; progress: Progress; docNames?: string[] } = $props();

	// The stage only draws an illustration. The share of lit particles follows the same reported progress as the
	// progress bar below, and a pulse runs each time a new report arrives. Nothing here comes from model activations.
	type Palette = { hue: number; span: number; sat: number; lit: number; dim: number; ring: number; ringOpacity: number };
	const PALETTES: Record<JobStatus, Palette> = {
		running: { hue: 0.43, span: 0.16, sat: 0.7, lit: 0.6, dim: 0.17, ring: 0x5eead4, ringOpacity: 0.28 },
		completed: { hue: 0.43, span: 0.16, sat: 0.7, lit: 0.62, dim: 0.17, ring: 0x6ee7b7, ringOpacity: 0.34 },
		queued: { hue: 0.58, span: 0.06, sat: 0.45, lit: 0.5, dim: 0.15, ring: 0x93c5fd, ringOpacity: 0.18 },
		paused: { hue: 0.72, span: 0.08, sat: 0.5, lit: 0.6, dim: 0.15, ring: 0xa78bfa, ringOpacity: 0.24 },
		failed: { hue: 0.99, span: 0.03, sat: 0.6, lit: 0.55, dim: 0.13, ring: 0xf87171, ringOpacity: 0.22 },
		cancelled: { hue: 0, span: 0, sat: 0, lit: 0.5, dim: 0.12, ring: 0xa1a1aa, ringOpacity: 0.16 }
	};
	type Stage = { sync: (s: { status: JobStatus; fill: number; animating: boolean; report: number | null }) => void };

	let canvas: HTMLCanvasElement;
	let section: HTMLElement;
	let stage: Stage | null = $state.raw(null);
	let available = $state(true);
	let reducedMotion = $state(false);
	let motionChoice: boolean | null = $state(null);
	let hidden = $state(false);
	let onScreen = $state(true);

	const motionOn = $derived(motionChoice ?? !reducedMotion);
	const running = $derived(status === 'running');
	const animating = $derived(running && available && motionOn && !hidden && onScreen);
	const fill = $derived(status === 'completed' ? 1 : status === 'queued' ? 0 : (progress?.percent ?? 0) / 100);
	const short = (s: string) => (s.length > 28 ? `${s.slice(0, 27)}…` : s);
	const phrases = $derived(
		type === 'train'
			? ['Read a training question', 'Compare with the Teacher’s answer', 'Adjust the Student a little']
			: [
					docNames.length ? `Read a section of ${short(docNames[0])}` : 'Follow the PROMPT',
					'Write a question and its answer',
					'Re-solve it to check'
				]
	);
	const actor = $derived(type === 'train' ? 'Student · learning from examples' : 'Teacher · writing questions');
	const headline = $derived.by(() => {
		switch (status) {
			case 'queued':
				return 'Waiting for the shared Mac Studio.';
			case 'paused':
				return 'Paused. Saved progress is kept.';
			case 'completed':
				return type === 'train' ? 'Training finished.' : 'The question set is ready.';
			case 'failed':
				return 'Stopped with an error.';
			case 'cancelled':
				return 'Cancelled.';
			default:
				return type === 'train' ? 'The Student is learning.' : 'The Teacher is writing questions.';
		}
	});
	const detail = $derived(
		progress
			? [progress.phaseLabel, progress.percent != null ? `${progress.percent}%` : null].filter(Boolean).join(' · ')
			: running
				? 'Waiting for the first progress report'
				: ''
	);

	$effect(() => {
		stage?.sync({ status, fill, animating, report: progress?.done ?? null });
	});

	onMount(() => {
		let disposed = false;
		let cleanup = () => {};
		const media = window.matchMedia('(prefers-reduced-motion: reduce)');
		const motionChanged = () => (reducedMotion = media.matches);
		const visibilityChanged = () => (hidden = document.hidden);
		motionChanged();
		visibilityChanged();
		media.addEventListener('change', motionChanged);
		document.addEventListener('visibilitychange', visibilityChanged);
		const seen = new IntersectionObserver(([e]) => (onScreen = e.isIntersecting));
		seen.observe(section);

		async function setup() {
			const THREE = await import('three');
			if (disposed) return;
			const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
			renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
			const scene = new THREE.Scene();
			const camera = new THREE.OrthographicCamera(-5, 5, 1.9, -1.9, 0.1, 100);
			camera.position.set(0, 0, 9);
			const group = new THREE.Group();
			group.rotation.set(0.2, 0.6, 0.08);
			scene.add(group);

			// Fibonacci sphere of 900 points (the original Moonshine globe). Index 0 is the top, so filling from the
			// highest index lights the sphere from the bottom up.
			const N = 900;
			const positions = new Float32Array(N * 3);
			const colors = new Float32Array(N * 3);
			for (let i = 0; i < N; i++) {
				const y = 1 - (i / (N - 1)) * 2;
				const radius = Math.sqrt(1 - y * y);
				const angle = i * Math.PI * (3 - Math.sqrt(5));
				positions.set([Math.cos(angle) * radius * 2, y * 2, Math.sin(angle) * radius * 2], i * 3);
			}
			const geometry = new THREE.BufferGeometry();
			geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
			geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
			const material = new THREE.PointsMaterial({ size: 0.045, vertexColors: true, transparent: true, opacity: 0.9, blending: THREE.AdditiveBlending });
			group.add(new THREE.Points(geometry, material));
			const ringGeometry = new THREE.TorusGeometry(2.65, 0.006, 8, 160);
			const ringMaterial = new THREE.MeshBasicMaterial({ transparent: true });
			for (let i = 0; i < 3; i++) {
				const ring = new THREE.Mesh(ringGeometry, ringMaterial);
				ring.rotation.set(i * 0.75, i * 0.6, i * 0.3);
				group.add(ring);
			}
			const pulseGeometry = new THREE.RingGeometry(1.85, 1.87, 100);
			const pulseMaterial = new THREE.MeshBasicMaterial({ color: 0xa7f3d0, transparent: true, opacity: 0, side: THREE.DoubleSide });
			const pulseRing = new THREE.Mesh(pulseGeometry, pulseMaterial);
			scene.add(pulseRing);

			let palette = PALETTES.queued;
			let targetFill = 0;
			let shownFill = 0;
			let lastReport: number | null = null;
			let time = 0;
			let pulseStarted = -10;
			let raf = 0;
			let last = 0;
			const color = new THREE.Color();
			const PULSE = 1.8;

			function paint() {
				const age = time - pulseStarted;
				const pulse = age < PULSE ? Math.sin((age / PULSE) * Math.PI) : 0;
				const front = (age / PULSE) * 3 - 1.5;
				const edge = 1 - shownFill;
				for (let i = 0; i < N; i++) {
					const lit = shownFill >= 1 ? 1 : Math.min(1, Math.max(0, (i / N - edge) / 0.03));
					const wave = age < PULSE ? Math.max(0, 1 - Math.abs(positions[i * 3] / 2 - front) * 3) : 0;
					const hue = palette.hue + (i / N) * palette.span;
					const sat = palette.sat * (0.5 + lit * 0.5) - wave * 0.4;
					const light = palette.dim + (palette.lit - palette.dim) * lit + wave * 0.3;
					color.setHSL(hue, Math.max(0, sat), light);
					colors.set([color.r, color.g, color.b], i * 3);
				}
				geometry.attributes.color.needsUpdate = true;
				material.size = 0.045 + pulse * 0.025;
				pulseMaterial.opacity = pulse * 0.55;
				pulseRing.scale.setScalar(group.scale.x * (1 + Math.min(age, PULSE) * 0.65));
				renderer.render(scene, camera);
			}

			function tick(now: number) {
				raf = requestAnimationFrame(tick);
				if (now - last < 32) return;
				const dt = Math.min((now - last) / 1000, 0.06);
				last = now;
				time += dt;
				shownFill += (targetFill - shownFill) * Math.min(1, dt * 2);
				group.rotation.y += dt * 0.12;
				group.rotation.z = 0.08 + Math.sin(time * 0.1) * 0.12;
				paint();
			}
			function stop() {
				cancelAnimationFrame(raf);
				raf = 0;
			}

			// Fixed vertical extent; the globe sits at 70% of the width, where the text stream converges.
			const resize = new ResizeObserver(() => {
				const { width, height } = canvas.getBoundingClientRect();
				if (!width || !height) return;
				renderer.setSize(width, height, false);
				const right = (1.9 * width) / height;
				camera.left = -right;
				camera.right = right;
				camera.updateProjectionMatrix();
				const scale = Math.min(0.62, (0.6 * right) / 2.75);
				group.scale.setScalar(scale);
				group.position.x = pulseRing.position.x = right * 0.4;
				paint();
			});
			resize.observe(canvas);
			const contextLost = (e: Event) => {
				e.preventDefault();
				stop();
				stage = null;
				available = false;
			};
			canvas.addEventListener('webglcontextlost', contextLost);

			stage = {
				sync(s) {
					palette = PALETTES[s.status];
					ringMaterial.color.setHex(palette.ring);
					ringMaterial.opacity = palette.ringOpacity;
					targetFill = Math.min(1, Math.max(0, s.fill));
					if (s.report !== null && lastReport !== null && s.report !== lastReport && s.animating) pulseStarted = time;
					lastReport = s.report;
					if (s.animating) {
						if (!raf) {
							last = performance.now();
							raf = requestAnimationFrame(tick);
						}
					} else {
						// Inactive: draw one still frame and leave the GPU idle.
						stop();
						shownFill = targetFill;
						pulseStarted = -10;
						paint();
					}
				}
			};
			cleanup = () => {
				stop();
				stage = null;
				resize.disconnect();
				canvas.removeEventListener('webglcontextlost', contextLost);
				pulseGeometry.dispose();
				pulseMaterial.dispose();
				geometry.dispose();
				material.dispose();
				ringGeometry.dispose();
				ringMaterial.dispose();
				renderer.dispose();
				renderer.forceContextLoss();
			};
		}
		setup().catch(() => (available = false));
		return () => {
			disposed = true;
			media.removeEventListener('change', motionChanged);
			document.removeEventListener('visibilitychange', visibilityChanged);
			seen.disconnect();
			cleanup();
		};
	});
</script>

<section
	bind:this={section}
	class="observatory status-{status} relative mt-5 h-[340px] overflow-hidden rounded-lg border border-emerald-900/60 md:h-[260px]"
	class:stopped={!animating}
	aria-label="Job activity"
>
	<canvas bind:this={canvas} class="absolute inset-x-0 top-20 h-[190px] w-full md:top-0 md:h-full" class:invisible={!available} aria-hidden="true"></canvas>
	{#if !available}
		<div class="fallback-orb" aria-hidden="true"></div>
	{/if}
	{#if running}
		<div class="text-stream" aria-hidden="true">
			{#each [0, 1, 2] as lane (lane)}
				{@const words = phrases[lane].split(/\s+/).slice(0, 6)}
				<div class="packet" style:--lane={lane} style:--delay={`${lane * -2.7}s`}>
					{#each words as word, index (index)}
						<span
							class="token"
							style:--scatter={`${(index % 2 ? 1 : -1) * (10 + index * 5)}px`}
							style:--twist={`${(index - 2) * 7}deg`}
							style:--offset={`${words.slice(0, index).join(' ').length * 6.6 + index * 10}px`}>{word}</span
						>
					{/each}
				</div>
			{/each}
		</div>
	{/if}

	<div class="pointer-events-none absolute left-5 top-4 max-w-[90%] md:max-w-[58%]">
		<p class="flex items-center gap-2 text-[11px] uppercase tracking-[0.2em] text-emerald-300">
			<span class="dot h-1.5 w-1.5 rounded-full"></span>Studio / {STATUS_LABELS[status]}
		</p>
		<p class="mt-2 text-base font-medium tracking-tight text-zinc-100 md:text-xl">{headline}</p>
		{#if detail}<p class="mt-0.5 text-xs text-zinc-400">{detail}</p>{/if}
	</div>
	<p class="model-label">{actor}</p>
	<div class="absolute bottom-3 left-5 right-5 flex items-center justify-between gap-3 text-[11px] text-zinc-500">
		<span>{available ? 'Illustration · follows reported progress, not model activations' : '3D view unavailable · progress below still updates'}</span>
		{#if running}
			<button
				class="shrink-0 rounded border border-zinc-700 px-2 py-1 text-zinc-300 hover:bg-white/5"
				onclick={() => (motionChoice = !motionOn)}>{motionOn ? 'Pause motion' : 'Play motion'}</button
			>
		{/if}
	</div>
</section>

<style>
	.observatory { background: radial-gradient(ellipse at 70% 50%, #064e3b40, transparent 55%), #0b1012; }
	.status-paused { border-color: #4c1d9599; }
	.status-failed { border-color: #7f1d1d99; }
	.status-cancelled { border-color: #3f3f46; }
	.dot { background: #34d399; }
	.status-queued .dot { background: #60a5fa; }
	.status-paused .dot { background: #a78bfa; }
	.status-failed .dot { background: #f87171; }
	.status-cancelled .dot { background: #71717a; }
	.text-stream { position: absolute; inset: 0; pointer-events: none; overflow: hidden; mask-image: linear-gradient(to right, transparent, black 6%, black 90%, transparent); }
	.packet { position: absolute; left: 0; width: 100%; top: calc(44% + var(--lane) * 11%); display: flex; gap: 5px; white-space: nowrap; animation: feed 8.1s linear infinite; animation-delay: var(--delay); }
	.token { display: inline-block; padding: 3px 5px; border: 1px solid #6ee7b735; background: #08251fe6; border-radius: 4px; font: 11px ui-monospace, monospace; color: #a7f3d0; box-shadow: 0 0 12px #34d39912; animation: tokenize 8.1s linear infinite; animation-delay: var(--delay); }
	.stopped .packet, .stopped .token { animation-play-state: paused; }
	.model-label { position: absolute; left: 1.25rem; bottom: 3.8rem; font: 10px ui-monospace, monospace; text-transform: uppercase; letter-spacing: 0.12em; color: #6ee7b7a6; white-space: nowrap; pointer-events: none; }
	@media (min-width: 768px) { .model-label { left: 70%; bottom: 2.6rem; transform: translateX(-50%); } }
	.fallback-orb { position: absolute; left: 70%; top: 50%; width: 9rem; height: 9rem; transform: translate(-50%, -50%); border-radius: 9999px; background: radial-gradient(circle, #6ee7b733 1px, transparent 1.5px) 0 0 / 9px 9px, radial-gradient(circle at 40% 35%, #34d39926, transparent 70%); box-shadow: 0 0 0 1px #5eead433, 0 0 40px #34d39914; }
	@keyframes feed { 0% { top: calc(44% + var(--lane) * 11%); transform: translateX(-45%); opacity: 0; } 12% { opacity: 0.9; } 46% { opacity: 0.9; } 72%, 100% { top: 50%; transform: translateX(70%); opacity: 0; } }
	@keyframes tokenize { 0%, 28% { transform: translateY(0) scale(1); border-color: transparent; background: #08251fbb; } 43% { transform: translateY(var(--scatter)) rotate(var(--twist)) scale(0.95); border-color: #6ee7b770; } 72%, 100% { transform: translateX(calc(-1 * var(--offset))) scale(0.2); border-color: #6ee7b7; } }
</style>

<script lang="ts">
	import { onMount } from 'svelte';
	import type { JobRecord } from '$lib/types';
	import { trainingProgress } from '$lib/training-progress';
	let { job, log, excerpts = [] }: { job: JobRecord; log: string; excerpts?: string[] } = $props();
	let canvas: HTMLCanvasElement;
	let paused = $state(false);
	let available = $state(true);
	let hidden = $state(false);
	const distilling = $derived(job.type === 'distill' && Number(job.params.ALPHA ?? 0.5) !== 0);
	const phrases = $derived(excerpts.length ? excerpts : ['Explain how a gear turns', 'Connect the ideas together', 'Learn from this example']);
	const flowing = $derived(job.status === 'running' && !paused && !hidden);
	const progress = $derived(trainingProgress(log, job.params.ITERS));
	const training = $derived(job.type === 'finetune' || job.type === 'distill');
	const curve = $derived.by(() => {
		const points = progress.samples;
		if (points.length < 2) return '';
		const lo = Math.min(...points.map(p => p.loss));
		const hi = Math.max(...points.map(p => p.loss));
		const first = points[0].step;
		const span = points[points.length - 1].step - first || 1;
		return points.map(p => `${8 + (p.step - first) / span * 584},${106 - (p.loss - lo) / (hi - lo || 1) * 90}`).join(' ');
	});

	onMount(() => {
		let disposed = false;
		let cleanup = () => {};
		const visibilityChanged = () => { hidden = document.hidden; };
		visibilityChanged();
		document.addEventListener('visibilitychange', visibilityChanged);
		async function setup() {
			const THREE = await import('three');
			if (disposed) return;
			const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
			renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
			const scene = new THREE.Scene();
			const camera = new THREE.OrthographicCamera(-5, 5, 3.5, -3.5, 0.1, 100);
			camera.position.set(0, 0, 9);
			const group = new THREE.Group();
			scene.add(group);
			group.position.x = 2;
			group.scale.setScalar(distilling ? 0.48 : 0.62);
			const positions = new Float32Array(900 * 3);
			const colors = new Float32Array(900 * 3);
			for (let i = 0; i < 900; i++) {
				const y = 1 - (i / 899) * 2;
				const radius = Math.sqrt(1 - y * y);
				const angle = i * Math.PI * (3 - Math.sqrt(5));
				positions.set([Math.cos(angle) * radius * 2, y * 2, Math.sin(angle) * radius * 2], i * 3);
				const color = new THREE.Color().setHSL(0.43 + (i / 900) * 0.16, 0.7, 0.6);
				colors.set([color.r, color.g, color.b], i * 3);
			}
			const geometry = new THREE.BufferGeometry();
			geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
			geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
			const material = new THREE.PointsMaterial({ size: 0.045, vertexColors: true, transparent: true, opacity: 0.85, blending: THREE.AdditiveBlending });
			group.add(new THREE.Points(geometry, material));
			const ringGeometry = new THREE.TorusGeometry(2.65, 0.006, 8, 160);
			const ringMaterial = new THREE.MeshBasicMaterial({ color: 0x5eead4, transparent: true, opacity: 0.25 });
			for (let i = 0; i < 3; i++) {
				const ring = new THREE.Mesh(ringGeometry, ringMaterial);
				ring.rotation.set(i * 0.75, i * 0.6, i * 0.3);
				group.add(ring);
			}
			const teacher = distilling ? group.clone() : null;
			if (teacher) {
				teacher.position.x = -2.2;
				teacher.scale.setScalar(0.68);
				scene.add(teacher);
			}
			const pulseGeometry = new THREE.RingGeometry(1.85, 1.87, 100);
			const pulseMaterial = new THREE.MeshBasicMaterial({ color: 0xa7f3d0, transparent: true, opacity: 0, side: THREE.DoubleSide });
			const pulseRing = new THREE.Mesh(pulseGeometry, pulseMaterial);
			pulseRing.position.x = 2;
			scene.add(pulseRing);
			const media = window.matchMedia('(prefers-reduced-motion: reduce)');
			paused = media.matches;
			const motionChanged = () => { paused = media.matches; };
			media.addEventListener('change', motionChanged);
			const resize = new ResizeObserver(() => {
				const { width, height } = canvas.getBoundingClientRect();
				renderer.setSize(width, height, false);
				camera.top = 5 * height / Math.max(1, width);
			camera.bottom = -camera.top;
				camera.updateProjectionMatrix();
				renderer.render(scene, camera);
			});
			resize.observe(canvas);
			let frame = 0;
			let last = 0;
			let animationTime = 0;
			let lastStep = progress.latest?.step;
			let pulseStarted = -10;
			const waveColor = new THREE.Color();
			function render(now: number) {
				frame = requestAnimationFrame(render);
				if (!flowing) { last = now; return; }
				if (now - last < 32) return;
				animationTime += Math.min((now - last) / 1000, 0.06);
				last = now;
				if (progress.latest?.step !== lastStep) {
					lastStep = progress.latest?.step;
					if (lastStep !== undefined) pulseStarted = animationTime;
				}
				const pulseAge = animationTime - pulseStarted;
				const pulse = pulseAge < 1.8 ? Math.sin(pulseAge / 1.8 * Math.PI) : 0;
				material.size = 0.045 + pulse * 0.025;
				pulseMaterial.opacity = pulse * 0.55;
				pulseRing.scale.setScalar(0.65 + Math.min(pulseAge, 1.8) * 0.4);
				for (let i = 0; i < 900; i++) {
					const wave = pulseAge < 1.8 ? Math.max(0, 1 - Math.abs(positions[i * 3] / 2 - (pulseAge / 1.8 * 3 - 1.5)) * 3) : 0;
					waveColor.setHSL(0.43 + (i / 900) * 0.16, 0.7 - wave * 0.5, 0.6 + wave * 0.35);
					colors.set([waveColor.r, waveColor.g, waveColor.b], i * 3);
				}
				geometry.attributes.color.needsUpdate = true;
				group.rotation.y += 0.004;
				group.rotation.z = Math.sin(animationTime * 0.1) * 0.12;
				if (teacher) { teacher.rotation.y -= 0.002; teacher.rotation.z = -0.2; }
				renderer.render(scene, camera);
			}
			frame = requestAnimationFrame(render);
			cleanup = () => {
				cancelAnimationFrame(frame);
				resize.disconnect();
				media.removeEventListener('change', motionChanged);
				pulseGeometry.dispose(); pulseMaterial.dispose(); geometry.dispose(); material.dispose(); ringGeometry.dispose(); ringMaterial.dispose(); renderer.dispose();
			};
		}
		setup().catch(() => { available = false; });
		return () => { disposed = true; document.removeEventListener('visibilitychange', visibilityChanged); cleanup(); };
	});
</script>

<section class="observatory mt-6 overflow-hidden rounded-2xl border border-emerald-900/60" aria-label="Run activity">
	<div class="grid md:grid-cols-[1fr_1.1fr]">
		<div class="network-stage relative min-h-[360px]" class:distilling class:stopped={!flowing}>
			<canvas bind:this={canvas} class="absolute inset-0 h-full w-full" aria-hidden="true"></canvas>
			<div class="absolute left-5 top-5 flex items-center gap-2 text-xs uppercase tracking-[0.2em] text-emerald-300"><span class="h-1.5 w-1.5 rounded-full bg-emerald-400"></span>Studio / {job.status}</div>
			{#if job.status === 'running'}
				<div class="text-stream" aria-hidden="true">
					{#each [0, 1, 2] as lane}
						<div class="packet" style:--lane={lane} style:--delay={`${lane * -2.7}s`}>
							{#each phrases[lane % phrases.length].split(/\s+/).slice(0, 6) as word, index}
								<span class="token" style:--scatter={`${(index % 2 ? 1 : -1) * (10 + index * 5)}px`} style:--twist={`${(index - 2) * 7}deg`} style:--offset={`${phrases[lane % phrases.length].split(/\s+/).slice(0, index).join(' ').length * 6.6 + index * 10}px`}>{word}</span>
							{/each}
						</div>
					{/each}
				</div>
			{/if}
			<div class="model-label student">{distilling ? 'Student' : training ? 'Learning from examples' : 'Processing text'}</div>
			{#if distilling}<div class="model-label teacher">Teacher</div>{/if}
			<p class="absolute left-5 top-12 text-[10px] text-zinc-500">{job.status === 'queued' ? 'Text flow begins when the job starts' : excerpts.length ? 'Input excerpts · illustrative flow, not the current batch' : 'Example text · illustrative flow'}</p>
			<div class="absolute bottom-4 left-5 right-5 flex items-center justify-between gap-3 text-[11px] text-zinc-500">
				<span>{available ? 'Ambient visualization · not model activations' : '3D unavailable · metrics still update'}</span>
				{#if available}<button class="rounded border border-zinc-700 px-2 py-1 text-zinc-300 hover:bg-white/5" onclick={() => paused = !paused}>{paused ? 'Play motion' : 'Pause motion'}</button>{/if}
			</div>
		</div>
		<div class="relative p-6 md:py-8 md:pr-8">
			<p class="text-xs uppercase tracking-[0.2em] text-zinc-500">{training ? 'Training signal' : 'Run activity'}</p>
			<h2 class="mt-3 text-2xl font-medium tracking-tight text-zinc-100">{job.status === 'queued' ? 'Your turn is coming.' : progress.latest ? 'Learning, one step at a time.' : 'The Studio is at work.'}</h2>
			<p class="mt-2 text-sm leading-6 text-zinc-400">{job.status === 'queued' ? `Waiting for the shared Mac Studio${job.queuePosition ? ` · position ${job.queuePosition}` : ''}.` : progress.latest ? 'Metrics reported by the trainer. Updated every five seconds.' : training ? 'Waiting for the trainer’s first loss report. Loading models can take a few minutes.' : 'The live log below shows what the worker has reported.'}</p>
			{#if training}
				<div class="mt-6 grid grid-cols-2 gap-5">
					<div><p class="text-xs text-zinc-500">Reported step</p><p class="mt-1 font-mono text-xl text-zinc-100">{progress.latest?.step.toLocaleString() ?? '—'}<span class="text-sm text-zinc-500"> / {progress.total > 0 ? progress.total.toLocaleString() : '—'}</span></p></div>
					<div><p class="text-xs text-zinc-500">Training loss</p><p class="mt-1 font-mono text-xl text-emerald-300">{progress.latest?.loss.toFixed(4) ?? '—'}</p></div>
				</div>
				<div class="mt-4 h-1 overflow-hidden rounded-full bg-zinc-800" role="progressbar" aria-label="Reported training steps" aria-valuenow={progress.percent ?? undefined} aria-valuemin="0" aria-valuemax="100"><div class="h-full bg-emerald-400 transition-all duration-700" style:width={`${progress.percent ?? 0}%`}></div></div>
				{#if curve}
					<svg class="mt-4 h-28 w-full" viewBox="0 0 600 120" preserveAspectRatio="none" role="img" aria-label={`Recent training loss from ${progress.samples[0].loss.toFixed(4)} to ${progress.latest?.loss.toFixed(4)}`}><path d="M0 30H600 M0 70H600 M0 110H600" stroke="#ffffff0a" fill="none"/><polyline points={curve} fill="none" stroke="#6ee7b7" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>
					<p class="text-[11px] text-zinc-500">Recent loss · {progress.samples.length} reports from the log tail · lower is better</p>
				{/if}
			{/if}
		</div>
	</div>
</section>
<style>
	.observatory { background: radial-gradient(ellipse at 20% 45%, #064e3b40, transparent 60%), #0b1012; }
	.network-stage { overflow: hidden; }
	.text-stream { position: absolute; inset: 0; pointer-events: none; overflow: hidden; mask-image: linear-gradient(to right, transparent, black 6%, black 90%, transparent); }
	.packet { position: absolute; left: 0; width: 100%; top: calc(36% + var(--lane) * 12%); display: flex; gap: 5px; white-space: nowrap; animation: feed 8.1s linear infinite; animation-delay: var(--delay); }
	.token { display: inline-block; padding: 3px 5px; border: 1px solid #6ee7b735; background: #08251fe6; border-radius: 4px; font: 11px ui-monospace, monospace; color: #a7f3d0; box-shadow: 0 0 12px #34d39912; animation: tokenize 8.1s linear infinite; animation-delay: var(--delay); }
	.distilling .packet { animation-name: transfer; }
	.stopped .packet, .stopped .token { animation-play-state: paused; }
	.model-label { position: absolute; top: 78%; font: 10px ui-monospace, monospace; text-transform: uppercase; letter-spacing: 0.12em; color: #6ee7b7a6; transform: translateX(-50%); white-space: nowrap; }
	.student { left: 70%; } .teacher { left: 28%; }
	@keyframes feed { 0% { top: calc(36% + var(--lane) * 12%); transform: translateX(-45%); opacity: 0; } 12% { opacity: 0.9; } 46% { opacity: 0.9; } 72%, 100% { top: 50%; transform: translateX(70%); opacity: 0; } }
	@keyframes transfer { 0% { top: calc(36% + var(--lane) * 12%); transform: translateX(15%); opacity: 0; } 15% { opacity: 0.9; } 40% { opacity: 0.9; } 72%, 100% { top: 50%; transform: translateX(70%); opacity: 0; } }
	@keyframes tokenize { 0%, 28% { transform: translateY(0) scale(1); border-color: transparent; background: #08251fbb; } 43% { transform: translateY(var(--scatter)) rotate(var(--twist)) scale(0.95); border-color: #6ee7b770; } 72%, 100% { transform: translateX(calc(-1 * var(--offset))) scale(0.2); border-color: #6ee7b7; } }
	@media (prefers-reduced-motion: reduce) { .packet, .token { animation: none; } .packet { left: 5%; opacity: 0.45; } .distilling .packet { left: 20%; } }
</style>

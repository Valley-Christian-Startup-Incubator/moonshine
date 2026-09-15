/** Metrics reported by the trainer. Animation must never supply metric values. */
export interface TrainingSample { step: number; loss: number }
export function trainingProgress(log: string, configuredSteps: unknown) {
	const samples = new Map<number, TrainingSample>();
	let total = Number(configuredSteps) || 0;
	for (const line of log.split(/[\r\n]/)) {
		const match = line.match(/\biter\s+(\d+)(?:\/(\d+))?\s*:\s*(?:train\s+)?loss\s*[=:]?\s*([\d.eE+-]+)/i);
		if (!match) continue;
		const step = Number(match[1]);
		const loss = Number(match[3]);
		if (!Number.isFinite(loss) || loss < 0) continue;
		if (match[2]) total = Number(match[2]);
		// A resumed/restarted trainer can return to an earlier iteration.
		const lastStep = [...samples.keys()].at(-1);
		if (lastStep !== undefined && step < lastStep) samples.clear();
		samples.set(step, { step, loss });
	}
	const points = [...samples.values()];
	const latest = points.at(-1);
	return { samples: points, latest, total, percent: latest && total > 0 ? Math.min(100, latest.step / total * 100) : null };
}

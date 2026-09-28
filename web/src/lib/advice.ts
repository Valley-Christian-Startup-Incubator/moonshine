/**
 * Rules-based tips for raising the "Final answer correct" score of a Train + evaluate job.
 * Pure function of the job's own numbers (eval_report.json + validation-loss curve), so it
 * works for every finished train job without extra files.
 */

export interface AdviceTip {
	id: string;
	title: string;
	/** What to do. */
	action: string;
	/** Why, quoting this job's numbers. */
	why: string;
	priority: number;
}

export interface AdviceSummary {
	grade: string | null;
	baseGrade: string | null;
	trainedPct: string | null;
	basePct: string | null;
	changePts: number | null;
	/** Rough 95% noise band for the change, in percentage points. */
	noisePts: number | null;
	settings: string;
	tips: AdviceTip[];
}

/** US school scale: 97+ A+, 93 A, 90 A-, 87 B+, 83 B, 80 B-, ... below 60 F. */
export function letterGrade(acc: number | null | undefined): string | null {
	if (acc == null || !Number.isFinite(acc)) return null;
	const p = acc * 100;
	if (p >= 97) return 'A+';
	if (p >= 93) return 'A';
	if (p >= 90) return 'A-';
	if (p >= 87) return 'B+';
	if (p >= 83) return 'B';
	if (p >= 80) return 'B-';
	if (p >= 77) return 'C+';
	if (p >= 73) return 'C';
	if (p >= 70) return 'C-';
	if (p >= 67) return 'D+';
	if (p >= 63) return 'D';
	if (p >= 60) return 'D-';
	return 'F';
}

const pct = (v: number) => `${(v * 100).toFixed(1)}%`;
const n0 = (v: number) => Math.round(v).toLocaleString('en-US');
const f3 = (v: number) => (Math.round(v * 1000) / 1000).toString();
const pts = (v: number) => `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(1)} points`;
const lr = (v: number) => (v >= 0.001 ? String(v) : v.toExponential(0).replace('e-', 'e-'));
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

/** Student size in billions of parameters from its key (qwen3-0.6b -> 0.6). */
function studentSize(student: string | undefined): number | null {
	const m = /(\d+(?:\.\d+)?)b\b/i.exec(student ?? '');
	return m ? Number(m[1]) : null;
}

export function computeAdvice(
	report: Record<string, any> | null,
	validCurve: [number, number][] = []
): AdviceSummary | null {
	if (!report) return null;
	const base = num(report.untrained?.accuracy);
	const trained = num(report.trained?.accuracy);
	if (base == null || trained == null) return null;
	const change = trained - base;
	const changePts = change * 100;
	const rBase = num(report.untrained?.avg_reasoning_score);
	const rTrained = num(report.trained?.avg_reasoning_score);
	const vBase = num(report.validation_loss?.base?.loss);
	const vTrained = num(report.validation_loss?.trained?.loss);
	// compare the kept checkpoint's validation loss with the train loss at that checkpoint, not at the last step
	const trainLoss = num(report.train_loss_at_checkpoint) ?? num(report.final_train_loss);
	const nTrain = num(report.data?.n_train) ?? 0;
	const nEval = num(report.data?.n_eval) ?? num(report.trained?.graded) ?? 0;
	const size = studentSize(report.student);
	const sizeLabel = size != null ? `${size}B` : String(report.student ?? 'the student');
	const grounded = Boolean(report.grounded_grading?.matched);
	const t = report.training ?? {};
	const iters = num(t.iters);
	const epochs = num(t.epochs);
	const rate = num(t.learning_rate);
	const batch = num(t.batch_size);
	// train_eval.py keeps the checkpoint with the lowest validation loss (report.best_checkpoint)
	const bc = report.best_checkpoint ?? null;
	const keptEarly = bc && bc.used === 'best' && num(bc.step) != null ? (bc.step as number) : null;

	// approximate 95% band for a change between two scores on the same nEval questions
	const noisePts =
		nEval > 0 ? 1.96 * Math.sqrt((base * (1 - base) + trained * (1 - trained)) / nEval) * 100 : null;
	const withinNoise = noisePts != null && Math.abs(changePts) < noisePts;

	const settings = [
		iters != null ? `${n0(iters)} steps` : null,
		epochs != null ? `${epochs} epoch${epochs === 1 ? '' : 's'}` : null,
		batch != null ? `batch ${batch}` : null,
		rate != null ? `learning rate ${lr(rate)}` : null,
		t.num_layers != null ? `${t.num_layers} LoRA layers` : null,
		keptEarly != null && iters != null ? `kept the step-${n0(keptEarly)} checkpoint` : null
	]
		.filter(Boolean)
		.join(', ');

	// validation-loss curve during training: still falling at the end? bottomed out early?
	const vc = validCurve.filter((p) => p[0] > 1);
	let stillFalling = false;
	let bottomedEarly: { step: number; min: number; last: number } | null = null;
	if (vc.length >= 2) {
		const last = vc[vc.length - 1][1];
		const prev = vc[vc.length - 2][1];
		const minPt = vc.reduce((a, b) => (b[1] < a[1] ? b : a));
		stillFalling = last < prev * 0.98 && last <= minPt[1] + 1e-9;
		if (minPt !== vc[vc.length - 1] && last > minPt[1] * 1.05) bottomedEarly = { step: minPt[0], min: minPt[1], last };
	}
	const gapRatio = vTrained != null && trainLoss != null && trainLoss > 0 ? vTrained / trainLoss : null;
	const bigGap = gapRatio != null && gapRatio >= 2 && vTrained != null && vTrained > 0.4;
	const lossFellALot = vBase != null && vTrained != null && vTrained < vBase * 0.6;

	const tips: AdviceTip[] = [];
	const few = nTrain > 0 && nTrain < 1000;

	if (change < -0.004) {
		tips.push({
			id: 'dropped',
			title: 'Accuracy went down: check the answer key and question variety',
			action:
				'Open the Example answers and the Evaluation report and look for questions where the Teacher’s reference answer is wrong or ambiguous; drop or fix those, and generate questions that cover more varied topics and formats.',
			why: `Final answer correct went from ${pct(base)} to ${pct(trained)} (${pts(changePts)}, ${report.untrained?.correct ?? '?'} → ${report.trained?.correct ?? '?'} of ${n0(nEval)}).${withinNoise ? ` On ${n0(nEval)} questions a change under about ${noisePts!.toFixed(1)} points is within noise, so treat this as “no real change” rather than a real loss.` : ''}`,
			priority: 90
		});
	}

	if (base >= 0.8 && change < 0.03) {
		tips.push({
			id: 'high-start',
			title: 'The model already knew this subject: make the questions harder or more specific',
			action:
				'Generate harder or more niche questions (multi-step problems, the tricky cases students miss), or upload source documents so questions cover material the base model does not know. Near 100% on a general subject is capped by the Teacher’s own accuracy.',
			why: `The untrained ${sizeLabel} model already scored ${pct(base)} (${letterGrade(base)}), and training changed it by ${pts(changePts)}. Training mostly taught the answer style, not new knowledge.`,
			priority: 85
		});
	}

	if (few) {
		tips.push({
			id: 'few',
			title: `Generate more questions (1,000–3,000)`,
			action:
				'Run Generate Questions again for 1,000 to 3,000 questions on the same subject, then train on the combined set. More examples help most when the set is small.',
			why: `Only ${n0(nTrain)} questions were used for training and ${n0(nEval)} were held out${noisePts != null && nEval < 200 ? `; with ${n0(nEval)} test questions the score can swing about ±${noisePts.toFixed(0)} points by chance` : ''}.`,
			priority: nTrain < 300 ? 88 : 80
		});
	}

	if (size != null && size <= 1.7 && trained < 0.85) {
		tips.push({
			id: 'small-model',
			title: 'Try a bigger Student (4B or 8B)',
			action: 'Train the same questions on Qwen3 4B or 8B. Bigger students keep more knowledge and reason better, at the cost of longer training.',
			why: `This is the ${sizeLabel} Student and it finished at ${pct(trained)} (${letterGrade(trained)}).`,
			priority: 78
		});
	}

	if (keptEarly != null && bottomedEarly && !bigGap && !(change < -0.004 && lossFellALot)) {
		// the overfit end of the run was already thrown away; the tip is about saving time
		tips.push({
			id: 'kept-best',
			title: `Validation loss bottomed out at step ${n0(keptEarly)}: next time train fewer steps`,
			action: `Moonshine already kept the step-${n0(keptEarly)} checkpoint (lowest validation loss), so this model is not the over-trained final one. To save time next run, set Training Steps to about ${n0(Math.ceil((keptEarly * 1.2) / 10) * 10)}${epochs != null && epochs > 1 ? ' or use 1 pass over the data' : ''}. To get more out of the data, add more varied questions instead of training longer.`,
			why: `Validation loss was lowest at step ${n0(bottomedEarly.step)} (${f3(bottomedEarly.min)}) and ended higher (${f3(bottomedEarly.last)}) with ${settings}.`,
			priority: 50
		});
	} else if (bigGap || bottomedEarly || (change < -0.004 && lossFellALot)) {
		const reasons: string[] = [];
		if (bottomedEarly)
			reasons.push(
				keptEarly != null
					? `validation loss bottomed out early and ended higher (${f3(bottomedEarly.last)}); Moonshine already kept the step-${n0(keptEarly)} checkpoint, so train fewer steps next time`
					: `validation loss was lowest at step ${n0(bottomedEarly.step)} (${f3(bottomedEarly.min)}) and ended higher (${f3(bottomedEarly.last)})`
			);
		if (bigGap)
			reasons.push(
				`validation loss ${f3(vTrained!)} is ${gapRatio!.toFixed(1)}× the ${num(report.train_loss_at_checkpoint) != null ? 'train loss at that checkpoint' : 'final train loss'} ${f3(trainLoss!)}`
			);
		if (change < -0.004 && lossFellALot)
			reasons.push(`validation loss fell from ${f3(vBase!)} to ${f3(vTrained!)} while accuracy dropped, so it learned the wording more than the answers`);
		tips.push({
			id: 'overfit',
			title: few ? 'Signs of memorizing: add variety before training longer' : 'Signs of overfitting: train a little less',
			action: few
				? `With a small set the model memorizes quickly. Add more, and more varied, questions first; if you keep this set, use ${epochs != null && epochs <= 1 ? 'fewer steps' : '1 epoch'} or a lower learning rate (5e-5).`
				: epochs != null && epochs <= 1
					? `Use fewer steps${bottomedEarly ? ` (about ${n0(bottomedEarly.step)})` : ''} or a lower learning rate (5e-5), and make the questions more varied.`
					: `Use fewer epochs (for example 1${bottomedEarly && iters ? `, about ${n0(bottomedEarly.step)} steps` : ''}) or a lower learning rate (5e-5), and make the questions more varied.`,
			why: `Settings were ${settings}; ${reasons.join('; ')}.`,
			priority: 72
		});
	} else if (stillFalling || (vTrained != null && vTrained > 1.0)) {
		tips.push({
			id: 'undertrained',
			title: 'Train longer',
			action:
				epochs != null && epochs <= 1 && nTrain > 1000
					? 'Set Passes over the Data to 2 (big sets default to 1) or set more Training Steps. The model had not finished learning when training stopped; Moonshine keeps the best checkpoint, so training longer is low-risk.'
					: 'Set more epochs (for example 3) or more iterations. The model had not finished learning when training stopped; Moonshine keeps the best checkpoint, so training longer is low-risk.',
			why: stillFalling
				? `Validation loss was still falling at the last check (${vc
						.slice(-3)
						.map((p) => f3(p[1]))
						.join(' → ')}) with ${settings}.`
				: `Validation loss is still high at ${f3(vTrained!)} after ${settings}.`,
			priority: 70
		});
	}

	if (rBase != null && rTrained != null && rTrained < rBase - 0.1) {
		tips.push({
			id: 'reasoning',
			title: 'Ask the Teacher for fuller worked reasoning',
			action:
				'The Student copies the length and style of the training answers. Change the question-generation prompt so every completion shows its full worked reasoning before the final answer, then regenerate.',
			why: `Average reasoning score fell from ${rBase.toFixed(2)} to ${rTrained.toFixed(2)} (out of 5) after training; the training completions are probably shorter than the base model’s own explanations.`,
			priority: 66
		});
	}

	if (!grounded && trained < 0.95) {
		tips.push({
			id: 'grounded',
			title: 'For a niche subject, upload the official documents',
			action:
				'If this subject depends on specific material (a textbook chapter, handbook, rules or course notes), upload those documents in Generate Questions so questions and grading follow the source.',
			why: `These questions came from the Teacher’s general knowledge (no source documents were used), and the trained score is ${pct(trained)}.`,
			priority: 45
		});
	}

	if (change >= 0.05 && !tips.some((x) => x.priority >= 80)) {
		tips.push({
			id: 'keep-going',
			title: 'The recipe works: scale it up',
			action: 'Keep these settings and add more questions on the topics it still misses (see the Teacher’s analysis below if present).',
			why: `Training raised the score by ${pts(changePts)}, from ${pct(base)} to ${pct(trained)}.`,
			priority: 60
		});
	}

	tips.sort((a, b) => b.priority - a.priority);
	return {
		grade: letterGrade(trained),
		baseGrade: letterGrade(base),
		trainedPct: pct(trained),
		basePct: pct(base),
		changePts,
		noisePts,
		settings,
		tips: tips.slice(0, 5)
	};
}

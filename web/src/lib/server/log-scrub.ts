import path from 'node:path';
import { open, readdir, stat } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { CONTROL_DIR, DISTILL_HOME, JOBS_DIR } from './moonshine-paths';

/**
 * Privacy scrub for the admin "delete a name" action: removes every log line
 * that mentions the deleted name (display form or the lowercase-hyphen slug
 * used in Ollama tags, e.g. "benjamin-mull"), one of that person's job IDs, or
 * one of their Ollama model tags.
 *
 * Logs are edited in place (same inode: read, filter, truncate, rewrite) because
 * launchd holds them open in O_APPEND mode for the queue runner and the web
 * app; replacing the file would leave those processes writing to a deleted
 * inode. Every *.log file directly in ~/.distill/logs is scrubbed
 * (queue.out/err, web.out/err, dagu.*), plus any extra files passed in.
 */
export const LOGS_DIR = path.join(DISTILL_HOME, 'logs');
const MAX_LOG_BYTES = 256 * 1024 * 1024;

export interface ScrubTerms {
	name: string;
	jobIds?: string[];
	modelTags?: string[];
}

export interface ScrubResult {
	files: number;
	linesRemoved: number;
	perFile: Record<string, number>;
}

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/** Same rule as scripts/moonshine_common.py slugify() (maxlen 24) plus the untruncated form. */
export function nameSlugs(name: string): string[] {
	const full = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
	const short = full.slice(0, 24).replace(/^-+|-+$/g, '');
	return [...new Set([full, short].filter((s) => s.length >= 2))];
}

/**
 * Line matcher for all terms. Names/slugs match case-insensitively on
 * letter/digit boundaries (so "Al" does not remove every line containing
 * "Already"; spaces, hyphens and underscores are interchangeable); job IDs and
 * model tags match exactly.
 */
export function buildScrubPattern(t: ScrubTerms): { test(line: string): boolean } | null {
	const nameParts: string[] = [];
	const words = [t.name.trim(), ...nameSlugs(t.name)].filter(Boolean);
	for (const w of new Set(words.map((x) => x.toLowerCase()))) {
		const body = w.split(/[\s_-]+/).filter(Boolean).map(escapeRe).join('[\\s_-]+');
		if (body) nameParts.push(`(?<![\\p{L}\\p{N}])${body}(?![\\p{L}\\p{N}])`);
	}
	const exactParts: string[] = [];
	for (const id of t.jobIds ?? []) if (id) exactParts.push(`(?<![A-Za-z0-9])${escapeRe(id)}(?![A-Za-z0-9])`);
	for (const tag of t.modelTags ?? []) if (tag) exactParts.push(escapeRe(tag));
	const res: RegExp[] = [];
	if (nameParts.length) res.push(new RegExp(nameParts.join('|'), 'iu'));
	if (exactParts.length) res.push(new RegExp(exactParts.join('|'), 'u'));
	if (!res.length) return null;
	return { test: (line: string) => res.some((r) => r.test(line)) };
}

/** Filter one file in place. Returns the number of lines removed. */
export async function scrubFileInPlace(file: string, re: { test(line: string): boolean }): Promise<number> {
	const st = await stat(file).catch(() => null);
	if (!st || !st.isFile() || st.size === 0 || st.size > MAX_LOG_BYTES) return 0;
	const fh = await open(file, 'r+');
	try {
		const filter = (text: string) => {
			const lines = text.split(/(?<=\n)/);
			const kept = lines.filter((l) => !re.test(l));
			return { out: kept.join(''), removed: lines.length - kept.length };
		};
		const buf = Buffer.alloc(st.size);
		await fh.read(buf, 0, st.size, 0);
		const first = filter(buf.toString('utf-8'));
		if (first.removed === 0) return 0;
		let removed = first.removed;
		let out = first.out;
		// Pick up anything appended while we were filtering, then rewrite quickly.
		const now = (await fh.stat()).size;
		if (now > st.size) {
			const extra = Buffer.alloc(now - st.size);
			await fh.read(extra, 0, extra.length, st.size);
			const second = filter(extra.toString('utf-8'));
			removed += second.removed;
			out += second.out;
		}
		const data = Buffer.from(out, 'utf-8');
		await fh.truncate(0);
		await fh.write(data, 0, data.length, 0);
		await fh.sync().catch(() => {});
		return removed;
	} finally {
		await fh.close();
	}
}

export async function scrubLogs(t: ScrubTerms, extraFiles: string[] = []): Promise<ScrubResult> {
	const re = buildScrubPattern(t);
	const result: ScrubResult = { files: 0, linesRemoved: 0, perFile: {} };
	if (!re) return result;
	const files = new Set(extraFiles);
	for (const f of await readdir(LOGS_DIR).catch(() => [] as string[])) {
		if (f.endsWith('.log')) files.add(path.join(LOGS_DIR, f));
	}
	for (const f of files) {
		try {
			const n = await scrubFileInPlace(f, re);
			result.files++;
			if (n) {
				result.linesRemoved += n;
				result.perFile[path.basename(f)] = n;
			}
		} catch {
			/* a log we cannot edit is skipped; never fail the delete over it */
		}
	}
	return result;
}

/**
 * The queue runner handles purge requests asynchronously (it logs "Control:
 * purge <id>", "Deleted Ollama model <tag>", "Purged job <id>" a moment after
 * the web app asks). Wait up to `timeoutMs` for those requests to be consumed,
 * then scrub; also schedule a few later passes to catch stragglers (e.g. a
 * running job that takes a while to stop).
 */
export async function scrubLogsAfterPurge(t: ScrubTerms, timeoutMs = 20_000): Promise<ScrubResult> {
	const ids = t.jobIds ?? [];
	const pending = () =>
		ids.some((id) => existsSync(path.join(CONTROL_DIR, `${id}.json`)) || existsSync(path.join(JOBS_DIR, id)));
	const deadline = Date.now() + timeoutMs;
	while (ids.length && pending() && Date.now() < deadline) await new Promise((r) => setTimeout(r, 500));
	await new Promise((r) => setTimeout(r, ids.length ? 750 : 0));
	const res = await scrubLogs(t);
	for (const delay of [10_000, 60_000, 300_000]) {
		setTimeout(() => void scrubLogs(t).catch(() => {}), delay).unref?.();
	}
	return res;
}

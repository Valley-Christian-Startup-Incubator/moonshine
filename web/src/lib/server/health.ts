import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { DISTILL_HOME } from './env';

const execFileAsync = promisify(execFile);

export async function getDiskUsage(): Promise<string> {
	try {
		const { stdout } = await execFileAsync('du', ['-sh', DISTILL_HOME]);
		return stdout.trim().split('\t')[0] ?? 'unknown';
	} catch {
		return 'unknown';
	}
}

import path from 'node:path';
import { stat, readFile, readdir } from 'node:fs/promises';
import { STUDENT_MODELS_DIR } from './moonshine-paths';

/** The fixed student list. Every student is a 4-bit MLX Qwen3; there are no bit options. */
export const STUDENTS = [
	{ id: 'qwen3-0.6b', label: 'Qwen3 0.6B', dir: 'Qwen3-0.6B-4bit', params: '0.6B' },
	{ id: 'qwen3-1.7b', label: 'Qwen3 1.7B', dir: 'Qwen3-1.7B-4bit', params: '1.7B' },
	{ id: 'qwen3-4b', label: 'Qwen3 4B', dir: 'Qwen3-4B-4bit', params: '4B' },
	{ id: 'qwen3-8b', label: 'Qwen3 8B', dir: 'Qwen3-8B-4bit', params: '8B' }
] as const;

export type StudentId = (typeof STUDENTS)[number]['id'];

export interface StudentInfo {
	id: StudentId;
	label: string;
	params: string;
	path: string;
	available: boolean;
}

export function isStudentId(value: unknown): value is StudentId {
	return typeof value === 'string' && STUDENTS.some((s) => s.id === value);
}

/** A folder is valid if it has config.json, tokenizer files and at least one safetensors file. */
async function isValidModelDir(dir: string): Promise<boolean> {
	try {
		const cfg = JSON.parse(await readFile(path.join(dir, 'config.json'), 'utf-8'));
		if (!cfg || typeof cfg !== 'object') return false;
		await stat(path.join(dir, 'tokenizer.json'));
		const files = await readdir(dir);
		return files.some((f) => f.endsWith('.safetensors'));
	} catch {
		return false;
	}
}

export async function listStudents(): Promise<StudentInfo[]> {
	return Promise.all(
		STUDENTS.map(async (s) => {
			const p = path.join(STUDENT_MODELS_DIR, s.dir);
			return { id: s.id, label: s.label, params: s.params, path: p, available: await isValidModelDir(p) };
		})
	);
}

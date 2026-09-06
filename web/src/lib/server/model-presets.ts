import path from 'node:path';
import os from 'node:os';
import { randomUUID } from 'node:crypto';
import {
	mkdir,
	readFile,
	rename,
	stat,
	unlink,
	writeFile
} from 'node:fs/promises';

export type StudentModelId = 'qwen-4b' | 'qwen-8b';

export interface ModelPresets {
	students: Array<{
		id: StudentModelId;
		label: string;
		available: boolean;
	}>;
	teacher: {
		label: string;
		available: boolean;
	};
}

export interface ModelPresetConfig {
	modelPresets: ModelPresets;
	paths: {
		students: Record<StudentModelId, string | undefined>;
		teacher: string | undefined;
	};
}

export type TeacherSettingsSource = 'saved' | 'environment' | 'none';

export interface TeacherSettings {
	path: string;
	source: TeacherSettingsSource;
	error?: string;
}

const TEACHER_CONFIG_FILENAME = 'teacher-model.json';
const TEACHER_DIRECTORY_HINT =
	'Expected a local Qwen MLX directory with config.json, tokenizer.json, and a usable safetensors checkpoint.';

function teacherConfigPath(): string {
	return path.join(
		process.env.DISTILL_HOME ?? path.join(os.homedir(), '.distill'),
		TEACHER_CONFIG_FILENAME
	);
}

function teacherValidationError(reason: string): Error {
	return new Error(`${TEACHER_DIRECTORY_HINT} ${reason}`);
}

function errorCode(error: unknown): string | undefined {
	if (typeof error !== 'object' || error === null || !('code' in error)) return undefined;
	const code = (error as { code?: unknown }).code;
	return typeof code === 'string' ? code : undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isShardBasename(value: string): boolean {
	return (
		value.length > 0 &&
		value !== '.' &&
		value !== '..' &&
		!value.includes('/') &&
		!value.includes('\\') &&
		!value.includes('\0') &&
		path.basename(value) === value
	);
}

async function requireRegularFile(filePath: string, description: string): Promise<void> {
	try {
		const file = await stat(filePath);
		if (!file.isFile()) throw new Error('not a regular file');
	} catch {
		throw teacherValidationError(`${description} is missing or is not a regular file.`);
	}
}

async function requireNonEmptyRegularFile(filePath: string, description: string): Promise<void> {
	try {
		const file = await stat(filePath);
		if (!file.isFile() || file.size === 0) throw new Error('not a non-empty regular file');
	} catch {
		throw teacherValidationError(`${description} is missing, is not a regular file, or is empty.`);
	}
}

async function validateTeacherModelPath(rawPath: string): Promise<string> {
	const modelPath = typeof rawPath === 'string' ? rawPath.trim() : '';
	if (!modelPath || !path.isAbsolute(modelPath)) {
		throw teacherValidationError('Provide the full absolute path to the model directory.');
	}

	try {
		const modelDirectory = await stat(modelPath);
		if (!modelDirectory.isDirectory()) throw new Error('not a directory');
	} catch {
		throw teacherValidationError('The supplied path must be an existing directory.');
	}

	const configPath = path.join(modelPath, 'config.json');
	await requireRegularFile(configPath, 'config.json');

	let config: unknown;
	try {
		config = JSON.parse(await readFile(configPath, 'utf8'));
	} catch {
		throw teacherValidationError('config.json must contain valid JSON.');
	}
	if (
		!isRecord(config) ||
		typeof config.model_type !== 'string' ||
		!config.model_type.toLowerCase().startsWith('qwen')
	) {
		throw teacherValidationError('config.json must contain a model_type string beginning with "qwen".');
	}

	await requireRegularFile(path.join(modelPath, 'tokenizer.json'), 'tokenizer.json');

	const indexPath = path.join(modelPath, 'model.safetensors.index.json');
	let indexExists = false;
	try {
		const indexFile = await stat(indexPath);
		indexExists = true;
		if (!indexFile.isFile()) throw teacherValidationError('model.safetensors.index.json must be a regular file.');
	} catch (error) {
		if (errorCode(error) !== 'ENOENT') throw error;
	}

	if (indexExists) {
		let index: unknown;
		try {
			index = JSON.parse(await readFile(indexPath, 'utf8'));
		} catch {
			throw teacherValidationError('model.safetensors.index.json must contain valid JSON.');
		}

		const weightMap = isRecord(index) ? index.weight_map : undefined;
		if (!isRecord(weightMap) || Object.keys(weightMap).length === 0) {
			throw teacherValidationError(
				'model.safetensors.index.json must contain a non-empty weight_map object.'
			);
		}

		const shardNames = new Set<string>();
		for (const shard of Object.values(weightMap)) {
			if (typeof shard !== 'string' || !isShardBasename(shard)) {
				throw teacherValidationError(
					'Every weight_map value must be a non-empty shard basename within the model directory.'
				);
			}
			shardNames.add(shard);
		}

		for (const shard of shardNames) {
			await requireNonEmptyRegularFile(path.join(modelPath, shard), `Indexed shard ${shard}`);
		}
	} else {
		await requireNonEmptyRegularFile(path.join(modelPath, 'model.safetensors'), 'model.safetensors');
	}

	return modelPath;
}

type StoredTeacherConfig =
	| { kind: 'missing' }
	| { kind: 'invalid'; error: string }
	| { kind: 'path'; path: string };

async function readStoredTeacherConfig(): Promise<StoredTeacherConfig> {
	let rawConfig: string;
	try {
		rawConfig = await readFile(teacherConfigPath(), 'utf8');
	} catch (error) {
		if (errorCode(error) === 'ENOENT') return { kind: 'missing' };
		return { kind: 'invalid', error: 'The saved teacher model configuration could not be read.' };
	}

	let parsed: unknown;
	try {
		parsed = JSON.parse(rawConfig);
	} catch {
		return { kind: 'invalid', error: 'The saved teacher model configuration is not valid JSON.' };
	}
	if (!isRecord(parsed) || typeof parsed.path !== 'string') {
		return { kind: 'invalid', error: 'The saved teacher model configuration must contain a path string.' };
	}

	return { kind: 'path', path: parsed.path.trim() };
}

async function settingsForPath(
	modelPath: string,
	source: Exclude<TeacherSettingsSource, 'none'>
): Promise<TeacherSettings> {
	try {
		return { path: await validateTeacherModelPath(modelPath), source };
	} catch (error) {
		return {
			path: modelPath,
			source,
			error: error instanceof Error ? error.message : String(error)
		};
	}
}

export async function getTeacherSettings(): Promise<TeacherSettings> {
	const stored = await readStoredTeacherConfig();
	if (stored.kind === 'invalid') {
		return { path: '', source: 'none', error: stored.error };
	}
	if (stored.kind === 'path') return settingsForPath(stored.path, 'saved');

	const environmentPath = process.env.MOONSHINE_QWEN_30B_PATH?.trim();
	if (!environmentPath) return { path: '', source: 'none' };
	return settingsForPath(environmentPath, 'environment');
}

export async function saveTeacherModelPath(raw: string): Promise<string> {
	const modelPath = await validateTeacherModelPath(raw);
	const configPath = teacherConfigPath();
	const temporaryPath = `${configPath}.${process.pid}.${randomUUID()}.tmp`;
	try {
		await mkdir(path.dirname(configPath), { recursive: true });
		await writeFile(temporaryPath, `${JSON.stringify({ path: modelPath })}\n`, {
			encoding: 'utf8',
			flag: 'wx',
			mode: 0o600
		});
		await rename(temporaryPath, configPath);
	} catch (error) {
		await unlink(temporaryPath).catch(() => undefined);
		throw error;
	}
	return modelPath;
}

async function resolveLocalModelPath(rawPath: string | undefined): Promise<string | undefined> {
	if (!rawPath || !path.isAbsolute(rawPath)) return undefined;

	try {
		return (await stat(rawPath)).isDirectory() ? rawPath : undefined;
	} catch {
		return undefined;
	}
}

export async function getModelPresetConfig(): Promise<ModelPresetConfig> {
	const [qwen4b, qwen8b, teacherSettings] = await Promise.all([
		resolveLocalModelPath(process.env.MOONSHINE_QWEN_4B_PATH),
		resolveLocalModelPath(process.env.MOONSHINE_QWEN_8B_PATH),
		getTeacherSettings()
	]);
	const qwen30b = teacherSettings.error || teacherSettings.source === 'none' ? undefined : teacherSettings.path;

	return {
		modelPresets: {
			students: [
				{ id: 'qwen-4b', label: 'Qwen 4B', available: qwen4b !== undefined },
				{ id: 'qwen-8b', label: 'Qwen 8B', available: qwen8b !== undefined }
			],
			teacher: { label: 'Qwen 30B', available: qwen30b !== undefined }
		},
		paths: {
			students: { 'qwen-4b': qwen4b, 'qwen-8b': qwen8b },
			teacher: qwen30b
		}
	};
}

export async function getModelPresets(): Promise<ModelPresets> {
	return (await getModelPresetConfig()).modelPresets;
}

import { strict as assert } from 'node:assert';
import {
	mkdir,
	mkdtemp,
	readFile,
	rm,
	stat,
	symlink,
	writeFile
} from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { test } from 'node:test';

import {
	getModelPresetConfig,
	getModelPresets,
	getStudentSettings,
	getTeacherSettings,
	saveStudentModelPath,
	saveTeacherModelPath
} from '../src/lib/server/model-presets.ts';

const ENV_KEYS = [
	'DISTILL_HOME',
	'MOONSHINE_QWEN_4B_PATH',
	'MOONSHINE_QWEN_8B_PATH',
	'MOONSHINE_QWEN_30B_PATH'
];

const unavailablePresets = {
	students: [
		{ id: 'qwen-4b', label: 'Qwen 4B', available: false },
		{ id: 'qwen-8b', label: 'Qwen 8B', available: false }
	],
	teacher: { label: 'Teacher model', available: false }
};

async function makeModelFixture(root, name, { modelType = 'qwen3', checkpoint = true } = {}) {
	const modelPath = path.join(root, name);
	await mkdir(modelPath, { recursive: true });
	await writeFile(path.join(modelPath, 'config.json'), JSON.stringify({ model_type: modelType }));
	await writeFile(path.join(modelPath, 'tokenizer.json'), '{}');
	if (checkpoint) await writeFile(path.join(modelPath, 'model.safetensors'), 'fake weights');
	return modelPath;
}

async function assertTeacherPathRejected(modelPath) {
	await assert.rejects(
		saveTeacherModelPath(modelPath),
		(error) => {
			assert.match(error.message, /Expected a local Qwen MLX directory/);
			return true;
		}
	);
}

async function assertStudentPathRejected(id, modelPath) {
	await assert.rejects(
		saveStudentModelPath(id, modelPath),
		(error) => {
			assert.match(error.message, /Expected a local Qwen MLX directory/);
			return true;
		}
	);
}

test('model presets expose only configured local model availability', async () => {
	const savedEnv = new Map(ENV_KEYS.map((key) => [key, process.env[key]]));
	const tempRoot = await mkdtemp(path.join(tmpdir(), 'moonshine-model-presets-'));

	try {
		for (const key of ENV_KEYS) delete process.env[key];
		process.env.DISTILL_HOME = path.join(tempRoot, 'distill');
		assert.deepEqual(await getTeacherSettings(), {
			path: '',
			label: 'Teacher model',
			source: 'none'
		});
		assert.deepEqual(await getModelPresets(), unavailablePresets);

		process.env.MOONSHINE_QWEN_4B_PATH = 'mlx-community/Qwen-4B';
		process.env.MOONSHINE_QWEN_8B_PATH = 'Qwen-8B';
		process.env.MOONSHINE_QWEN_30B_PATH = 'mlx-community/Qwen-30B';
		assert.deepEqual(await getModelPresets(), unavailablePresets);

		const missingPresets = {
			MOONSHINE_QWEN_4B_PATH: path.join(tempRoot, 'missing-qwen-4b'),
			MOONSHINE_QWEN_8B_PATH: path.join(tempRoot, 'missing-qwen-8b'),
			MOONSHINE_QWEN_30B_PATH: path.join(tempRoot, 'missing-qwen-30b')
		};
		for (const [key, value] of Object.entries(missingPresets)) process.env[key] = value;
		assert.deepEqual(await getModelPresets(), unavailablePresets);

		const qwen4bPath = await makeModelFixture(tempRoot, 'qwen-4b');
		const qwen8bPath = await makeModelFixture(tempRoot, 'qwen-8b');
		const qwen30bPath = await makeModelFixture(tempRoot, 'qwen-30b');
		process.env.MOONSHINE_QWEN_4B_PATH = qwen4bPath;
		process.env.MOONSHINE_QWEN_8B_PATH = qwen8bPath;
		process.env.MOONSHINE_QWEN_30B_PATH = qwen30bPath;

		const publicModelPresets = await getModelPresets();
		assert.deepEqual(publicModelPresets, {
			students: [
				{ id: 'qwen-4b', label: 'Qwen 4B', available: true },
				{ id: 'qwen-8b', label: 'Qwen 8B', available: true }
			],
			teacher: { label: 'qwen-30b', available: true }
		});
		assert.equal(JSON.stringify(publicModelPresets).includes(tempRoot), false);
	} finally {
		for (const [key, value] of savedEnv) {
			if (value === undefined) delete process.env[key];
			else process.env[key] = value;
		}
		await rm(tempRoot, { recursive: true, force: true });
	}
});

test('teacher path saves atomically and takes precedence over the environment', async () => {
	const savedEnv = new Map(ENV_KEYS.map((key) => [key, process.env[key]]));
	const tempRoot = await mkdtemp(path.join(tmpdir(), 'moonshine-teacher-save-'));

	try {
		process.env.DISTILL_HOME = path.join(tempRoot, 'distill');
		delete process.env.MOONSHINE_QWEN_4B_PATH;
		delete process.env.MOONSHINE_QWEN_8B_PATH;
		const environmentPath = await makeModelFixture(tempRoot, 'environment-teacher');
		const savedPath = await makeModelFixture(tempRoot, 'saved-teacher');
		process.env.MOONSHINE_QWEN_30B_PATH = environmentPath;

		assert.deepEqual(await getTeacherSettings(), {
			path: environmentPath,
			label: 'environment-teacher',
			source: 'environment'
		});

		assert.equal(await saveTeacherModelPath(` ${savedPath} `), savedPath);
		const configPath = path.join(process.env.DISTILL_HOME, 'teacher-model.json');
		assert.deepEqual(JSON.parse(await readFile(configPath, 'utf8')), { path: savedPath });
		assert.equal((await stat(configPath)).mode & 0o777, 0o600);
		assert.deepEqual(await getTeacherSettings(), {
			path: savedPath,
			label: 'saved-teacher',
			source: 'saved'
		});
		assert.equal((await getModelPresetConfig()).modelPresets.teacher.label, 'saved-teacher');
		assert.equal((await getModelPresetConfig()).paths.teacher, savedPath);

		await rm(path.join(savedPath, 'model.safetensors'));
		const brokenSettings = await getTeacherSettings();
		assert.equal(brokenSettings.path, savedPath);
		assert.equal(brokenSettings.label, 'Teacher model');
		assert.equal(brokenSettings.source, 'saved');
		assert.match(brokenSettings.error, /Expected a local Qwen MLX directory/);
		assert.equal((await getModelPresetConfig()).paths.teacher, undefined);
	} finally {
		for (const [key, value] of savedEnv) {
			if (value === undefined) delete process.env[key];
			else process.env[key] = value;
		}
		await rm(tempRoot, { recursive: true, force: true });
	}
});

test('teacher validation rejects hubs, non-Qwen folders, and missing checkpoints', async () => {
	const savedEnv = new Map(ENV_KEYS.map((key) => [key, process.env[key]]));
	const tempRoot = await mkdtemp(path.join(tmpdir(), 'moonshine-teacher-validation-'));

	try {
		process.env.DISTILL_HOME = path.join(tempRoot, 'distill');
		delete process.env.MOONSHINE_QWEN_30B_PATH;
		await assertTeacherPathRejected('mlx-community/Qwen-30B');
		await assertTeacherPathRejected(path.join(tempRoot, 'missing-model'));

		const nonQwenPath = await makeModelFixture(tempRoot, 'non-qwen', { modelType: 'llama' });
		await assertTeacherPathRejected(nonQwenPath);

		const missingCheckpointPath = await makeModelFixture(tempRoot, 'missing-checkpoint', {
			checkpoint: false
		});
		await assertTeacherPathRejected(missingCheckpointPath);

		const indexedPath = await makeModelFixture(tempRoot, 'indexed', { checkpoint: false });
		await writeFile(
			path.join(indexedPath, 'model.safetensors.index.json'),
			JSON.stringify({ weight_map: { 'model.embed_tokens.weight': 'model-00001.safetensors' } })
		);
		await writeFile(path.join(indexedPath, 'model-00001.safetensors'), 'fake shard');
		assert.equal(await saveTeacherModelPath(indexedPath), indexedPath);
		const savedConfig = JSON.parse(
			await readFile(path.join(process.env.DISTILL_HOME, 'teacher-model.json'), 'utf8')
		);

		const traversalPath = await makeModelFixture(tempRoot, 'traversal', { checkpoint: false });
		await writeFile(
			path.join(traversalPath, 'model.safetensors.index.json'),
			JSON.stringify({ weight_map: { tensor: '../outside.safetensors' } })
		);
		await writeFile(path.join(tempRoot, 'outside.safetensors'), 'outside');
		await assertTeacherPathRejected(traversalPath);
		assert.deepEqual(
			JSON.parse(await readFile(path.join(process.env.DISTILL_HOME, 'teacher-model.json'), 'utf8')),
			savedConfig
		);
	} finally {
		for (const [key, value] of savedEnv) {
			if (value === undefined) delete process.env[key];
			else process.env[key] = value;
		}
		await rm(tempRoot, { recursive: true, force: true });
	}
});

test('teacher validation accepts symlinked local model files', async () => {
	const savedEnv = new Map(ENV_KEYS.map((key) => [key, process.env[key]]));
	const tempRoot = await mkdtemp(path.join(tmpdir(), 'moonshine-teacher-symlink-'));

	try {
		process.env.DISTILL_HOME = path.join(tempRoot, 'distill');
		delete process.env.MOONSHINE_QWEN_30B_PATH;
		const sourcePath = await makeModelFixture(tempRoot, 'model-source');
		const linkedPath = path.join(tempRoot, 'linked-model');
		await mkdir(linkedPath);
		for (const filename of ['config.json', 'tokenizer.json', 'model.safetensors']) {
			await symlink(path.join(sourcePath, filename), path.join(linkedPath, filename));
		}
		assert.equal(await saveTeacherModelPath(linkedPath), linkedPath);
		assert.deepEqual(await getTeacherSettings(), {
			path: linkedPath,
			label: 'linked-model',
			source: 'saved'
		});
	} finally {
		for (const [key, value] of savedEnv) {
			if (value === undefined) delete process.env[key];
			else process.env[key] = value;
		}
		await rm(tempRoot, { recursive: true, force: true });
	}
});

test('corrupt saved teacher settings fail closed instead of using the environment', async () => {
	const savedEnv = new Map(ENV_KEYS.map((key) => [key, process.env[key]]));
	const tempRoot = await mkdtemp(path.join(tmpdir(), 'moonshine-teacher-corrupt-'));

	try {
		process.env.DISTILL_HOME = path.join(tempRoot, 'distill');
		const environmentPath = await makeModelFixture(tempRoot, 'environment-teacher');
		process.env.MOONSHINE_QWEN_30B_PATH = environmentPath;
		await mkdir(process.env.DISTILL_HOME, { recursive: true });
		await writeFile(path.join(process.env.DISTILL_HOME, 'teacher-model.json'), '{not-json');

		const settings = await getTeacherSettings();
		assert.equal(settings.path, '');
		assert.equal(settings.label, 'Teacher model');
		assert.equal(settings.source, 'none');
		assert.match(settings.error, /saved teacher model configuration/i);
		assert.equal((await getModelPresets()).teacher.available, false);
	} finally {
		for (const [key, value] of savedEnv) {
			if (value === undefined) delete process.env[key];
			else process.env[key] = value;
		}
		await rm(tempRoot, { recursive: true, force: true });
	}
});

test('teacher labels use the model name from Hugging Face cache paths', async () => {
	const savedEnv = new Map(ENV_KEYS.map((key) => [key, process.env[key]]));
	const tempRoot = await mkdtemp(path.join(tmpdir(), 'moonshine-teacher-cache-label-'));

	try {
		process.env.DISTILL_HOME = path.join(tempRoot, 'distill');
		delete process.env.MOONSHINE_QWEN_4B_PATH;
		delete process.env.MOONSHINE_QWEN_8B_PATH;
		const cachedPath = await makeModelFixture(
			tempRoot,
			path.join('models--mlx-community--Qwen3.5-35B-A3B-4bit', 'snapshots', 'revision-123')
		);
		process.env.MOONSHINE_QWEN_30B_PATH = cachedPath;

		assert.deepEqual(await getTeacherSettings(), {
			path: cachedPath,
			label: 'Qwen3.5-35B-A3B-4bit',
			source: 'environment'
		});
		assert.deepEqual((await getModelPresets()).teacher, {
			label: 'Qwen3.5-35B-A3B-4bit',
			available: true
		});
		assert.equal(JSON.stringify(await getModelPresets()).includes(cachedPath), false);
	} finally {
		for (const [key, value] of savedEnv) {
			if (value === undefined) delete process.env[key];
			else process.env[key] = value;
		}
		await rm(tempRoot, { recursive: true, force: true });
	}
});

test('student paths save to separate slots and override their environment paths', async () => {
	const savedEnv = new Map(ENV_KEYS.map((key) => [key, process.env[key]]));
	const tempRoot = await mkdtemp(path.join(tmpdir(), 'moonshine-student-save-'));

	try {
		process.env.DISTILL_HOME = path.join(tempRoot, 'distill');
		delete process.env.MOONSHINE_QWEN_30B_PATH;
		const environment4b = await makeModelFixture(tempRoot, 'environment-4b');
		const environment8b = await makeModelFixture(tempRoot, 'environment-8b');
		const saved4b = await makeModelFixture(tempRoot, 'saved-4b');
		process.env.MOONSHINE_QWEN_4B_PATH = environment4b;
		process.env.MOONSHINE_QWEN_8B_PATH = environment8b;

		assert.deepEqual(await getStudentSettings(), {
			'qwen-4b': { path: environment4b, label: 'environment-4b', source: 'environment' },
			'qwen-8b': { path: environment8b, label: 'environment-8b', source: 'environment' }
		});
		assert.deepEqual((await getModelPresetConfig()).paths.students, {
			'qwen-4b': environment4b,
			'qwen-8b': environment8b
		});

		assert.equal(await saveStudentModelPath('qwen-4b', ` ${saved4b} `), saved4b);
		const configPath = path.join(process.env.DISTILL_HOME, 'student-model-qwen-4b.json');
		assert.deepEqual(JSON.parse(await readFile(configPath, 'utf8')), { path: saved4b });
		assert.equal((await stat(configPath)).mode & 0o777, 0o600);
		assert.deepEqual(await getStudentSettings(), {
			'qwen-4b': { path: saved4b, label: 'saved-4b', source: 'saved' },
			'qwen-8b': { path: environment8b, label: 'environment-8b', source: 'environment' }
		});
		assert.deepEqual((await getModelPresetConfig()).paths.students, {
			'qwen-4b': saved4b,
			'qwen-8b': environment8b
		});

		await rm(path.join(saved4b, 'model.safetensors'));
		const broken = await getStudentSettings();
		assert.equal(broken['qwen-4b'].path, saved4b);
		assert.equal(broken['qwen-4b'].source, 'saved');
		assert.match(broken['qwen-4b'].error, /Expected a local Qwen MLX directory/);
		assert.equal(broken['qwen-8b'].path, environment8b);
		assert.deepEqual((await getModelPresetConfig()).paths.students, {
			'qwen-4b': undefined,
			'qwen-8b': environment8b
		});
	} finally {
		for (const [key, value] of savedEnv) {
			if (value === undefined) delete process.env[key];
			else process.env[key] = value;
		}
		await rm(tempRoot, { recursive: true, force: true });
	}
});

test('student settings validate saved and environment paths, fail closed, and reject invalid ids', async () => {
	const savedEnv = new Map(ENV_KEYS.map((key) => [key, process.env[key]]));
	const tempRoot = await mkdtemp(path.join(tmpdir(), 'moonshine-student-validation-'));

	try {
		process.env.DISTILL_HOME = path.join(tempRoot, 'distill');
		delete process.env.MOONSHINE_QWEN_30B_PATH;
		const environmentPath = await makeModelFixture(tempRoot, 'environment-4b');
		process.env.MOONSHINE_QWEN_4B_PATH = environmentPath;
		delete process.env.MOONSHINE_QWEN_8B_PATH;

		await assertStudentPathRejected('qwen-4b', path.join(tempRoot, 'missing-model'));
		const nonQwenPath = await makeModelFixture(tempRoot, 'non-qwen', { modelType: 'llama' });
		await assertStudentPathRejected('qwen-4b', nonQwenPath);
		const missingCheckpointPath = await makeModelFixture(tempRoot, 'missing-checkpoint', {
			checkpoint: false
		});
		await assertStudentPathRejected('qwen-4b', missingCheckpointPath);

		await mkdir(process.env.DISTILL_HOME, { recursive: true });
		await writeFile(path.join(process.env.DISTILL_HOME, 'student-model-qwen-4b.json'), '{not-json');
		const corrupt = await getStudentSettings();
		assert.equal(corrupt['qwen-4b'].path, '');
		assert.equal(corrupt['qwen-4b'].source, 'none');
		assert.match(corrupt['qwen-4b'].error, /saved student model configuration/i);
		assert.equal((await getModelPresetConfig()).paths.students['qwen-4b'], undefined);

		await writeFile(
			path.join(process.env.DISTILL_HOME, 'student-model-qwen-4b.json'),
			JSON.stringify({ path: path.join(tempRoot, 'missing-saved-model') })
		);
		const missingSaved = await getStudentSettings();
		assert.equal(missingSaved['qwen-4b'].source, 'saved');
		assert.equal(missingSaved['qwen-4b'].path, path.join(tempRoot, 'missing-saved-model'));
		assert.match(missingSaved['qwen-4b'].error, /Expected a local Qwen MLX directory/);
		assert.equal((await getModelPresetConfig()).paths.students['qwen-4b'], undefined);

		await assert.rejects(
			saveStudentModelPath('qwen-16b', path.join(tempRoot, 'missing-model')),
			(error) => {
				assert.match(error.message, /Invalid student model id/);
				return true;
			}
		);
	} finally {
		for (const [key, value] of savedEnv) {
			if (value === undefined) delete process.env[key];
			else process.env[key] = value;
		}
		await rm(tempRoot, { recursive: true, force: true });
	}
});

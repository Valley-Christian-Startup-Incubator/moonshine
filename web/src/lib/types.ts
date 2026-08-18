export type Team = 'FRC Robotics' | 'WarriorTides' | 'ISS Program' | 'Walleys Student Store';

export type JobType = 'prompt-gen' | 'teacher-gen' | 'finetune' | 'distill' | 'quantize';

export type JobStatus = 'queued' | 'running' | 'complete' | 'failed' | 'unknown';

export interface JobParams {
	[key: string]: string | number;
}

export interface JobRecord {
	id: string;
	team: Team;
	type: JobType;
	status: JobStatus;
	params: JobParams;
	inputFile: string;
	submittedAt: string;
	startedAt?: string;
	completedAt?: string;
	queuePosition?: number;
	outputPath?: string;
	error?: string;
}

export interface StatusFile {
	status: 'running' | 'complete' | 'failed';
	started_at: string;
	completed_at?: string;
	output_path?: string;
	error?: string;
}

export interface JobTypeFieldDef {
	name: string;
	label: string;
	type: 'text' | 'number';
	default: string | number;
	step?: number;
}

export const JOB_TYPES: { value: JobType; label: string; description: string }[] = [
	{
		value: 'prompt-gen',
		label: 'Prompt Generation',
		description: 'Generate candidate prompts from a JSONL config file.'
	},
	{
		value: 'teacher-gen',
		label: 'Teacher Generation',
		description: 'Run prompts through a teacher model to produce completions.'
	},
	{
		value: 'finetune',
		label: 'Fine-tune (LoRA, response-based)',
		description: 'SFT a student on the teacher’s generated text (next-token cross-entropy).'
	},
	{
		value: 'distill',
		label: 'Distill (LoRA, logit-based)',
		description:
			'Train a student to match the teacher’s output distribution via KL divergence, not just its text.'
	},
	{
		value: 'quantize',
		label: 'Quantize',
		description: 'Quantize a model for faster local inference.'
	}
];

export const JOB_TYPE_FIELDS: Record<JobType, JobTypeFieldDef[]> = {
	'prompt-gen': [],
	'teacher-gen': [
		{ name: 'MODEL_PATH', label: 'Teacher model path', type: 'text', default: 'mlx-community/Meta-Llama-3.1-8B-Instruct-4bit' },
		{ name: 'MAX_TOKENS', label: 'Max tokens', type: 'number', default: 512 },
		{ name: 'TEMPERATURE', label: 'Temperature', type: 'number', default: 0.7, step: 0.1 }
	],
	finetune: [
		{ name: 'MODEL_PATH', label: 'Base model path', type: 'text', default: 'mlx-community/Meta-Llama-3.1-8B-Instruct-4bit' },
		{ name: 'ADAPTER_PATH', label: 'Adapter output path', type: 'text', default: 'adapters' },
		{ name: 'ITERS', label: 'Iterations', type: 'number', default: 1000 },
		{ name: 'BATCH_SIZE', label: 'Batch size', type: 'number', default: 4 },
		{ name: 'LEARNING_RATE', label: 'Learning rate', type: 'number', default: 0.00001, step: 0.000001 }
	],
	distill: [
		{
			name: 'TEACHER_MODEL_PATH',
			label: 'Teacher model path',
			type: 'text',
			default: 'mlx-community/Meta-Llama-3.1-8B-Instruct'
		},
		{
			name: 'MODEL_PATH',
			label: 'Student (base) model path',
			type: 'text',
			default: 'mlx-community/Meta-Llama-3.1-8B-Instruct-4bit'
		},
		{ name: 'ADAPTER_PATH', label: 'Adapter output path', type: 'text', default: 'adapters' },
		{ name: 'ITERS', label: 'Iterations', type: 'number', default: 1000 },
		{ name: 'BATCH_SIZE', label: 'Batch size', type: 'number', default: 4 },
		{ name: 'LEARNING_RATE', label: 'Learning rate', type: 'number', default: 0.00001, step: 0.000001 },
		{ name: 'TEMPERATURE', label: 'Distillation temperature', type: 'number', default: 2.0, step: 0.1 },
		{
			name: 'ALPHA',
			label: 'KL weight (0=pure SFT, 1=pure distillation)',
			type: 'number',
			default: 0.5,
			step: 0.1
		},
		{ name: 'LORA_RANK', label: 'LoRA rank', type: 'number', default: 8 },
		{ name: 'LORA_LAYERS', label: 'LoRA layers (from the end)', type: 'number', default: 16 }
	],
	quantize: [
		{ name: 'MODEL_PATH', label: 'Model path', type: 'text', default: 'mlx-community/Meta-Llama-3.1-8B-Instruct' },
		{ name: 'Q_BITS', label: 'Quantization bits', type: 'number', default: 4 },
		{ name: 'Q_GROUP_SIZE', label: 'Group size', type: 'number', default: 64 }
	]
};

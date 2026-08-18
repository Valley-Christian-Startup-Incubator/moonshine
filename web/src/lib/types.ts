export type Team = 'FRC Robotics' | 'WarriorTides' | 'ISS Program' | 'Walleys Student Store';

export type JobType = 'prompt-gen' | 'teacher-gen' | 'finetune' | 'quantize';

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
		label: 'Fine-tune (LoRA)',
		description: 'LoRA fine-tune a student model on a training JSONL.'
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
	quantize: [
		{ name: 'MODEL_PATH', label: 'Model path', type: 'text', default: 'mlx-community/Meta-Llama-3.1-8B-Instruct' },
		{ name: 'Q_BITS', label: 'Quantization bits', type: 'number', default: 4 },
		{ name: 'Q_GROUP_SIZE', label: 'Group size', type: 'number', default: 64 }
	]
};

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
	help: string;
}

export interface JobTypeDef {
	value: JobType;
	label: string;
	description: string;
	requiresInput: boolean;
	inputLabel: string;
	inputHelp: string;
	inputExample?: string;
	prerequisite: string;
	output: string;
	nextStep: string;
	duration: string;
	warning?: string;
}

export const JOB_TYPES: JobTypeDef[] = [
	{
		value: 'prompt-gen',
		label: 'Generate prompts',
		description: 'Turn a list of topics into questions for a teacher model.',
		requiresInput: true,
		inputLabel: 'Topic file',
		inputHelp: 'Upload a JSONL file with one topic per line.',
		inputExample: '{"topic":"robotics","n":5}',
		prerequisite: 'None. This is usually the first step.',
		output: 'A JSONL file containing generated prompts.',
		nextStep: 'Use the downloaded file as the input for a Teacher answers job.',
		duration: 'Usually a few minutes.',
		warning: 'This generator is a starter template. Check with your instructor before using it for a project.'
	},
	{
		value: 'teacher-gen',
		label: 'Generate teacher answers',
		description: 'Have a model answer every prompt in your file.',
		requiresInput: true,
		inputLabel: 'Prompt file',
		inputHelp: 'Upload a JSONL file with one prompt per line.',
		inputExample: '{"prompt":"Explain how a gear ratio changes torque."}',
		prerequisite: 'A prompt file you created yourself or downloaded from Generate prompts.',
		output: 'A JSONL file containing each prompt and the teacher model\'s answer.',
		nextStep: 'Use the downloaded file to Fine-tune a model or run a Distillation experiment.',
		duration: 'A few minutes to several hours, depending on the number of prompts.'
	},
	{
		value: 'finetune',
		label: 'Fine-tune a model',
		description: 'Teach a model from saved prompt-and-answer examples. Recommended for most teams.',
		requiresInput: true,
		inputLabel: 'Training file',
		inputHelp: 'Upload the JSONL result from a completed Generate teacher answers job.',
		inputExample: '{"prompt":"What is torque?","completion":"Torque is a turning force."}',
		prerequisite: 'A completed Generate teacher answers job.',
		output: 'A LoRA adapter containing the model\'s new training.',
		nextStep: 'Download the adapter and test it against the original model.',
		duration: 'Up to half a day.'
	},
	{
		value: 'distill',
		label: 'Run a distillation experiment',
		description: 'Compare a teacher and student model during training. Intended for advanced experiments.',
		requiresInput: true,
		inputLabel: 'Training file',
		inputHelp: 'Upload the JSONL result from a completed Generate teacher answers job.',
		inputExample: '{"prompt":"What is torque?","completion":"Torque is a turning force."}',
		prerequisite: 'A completed Generate teacher answers job and compatible teacher and student models.',
		output: 'A LoRA adapter trained from the teacher model\'s answers and token probabilities.',
		nextStep: 'Download the adapter and compare it with a Fine-tune job that used the same data.',
		duration: 'Up to half a day.',
		warning: 'The teacher and student must use the same tokenizer. Ask your instructor if you are unsure.'
	},
	{
		value: 'quantize',
		label: 'Make a model smaller',
		description: 'Create a smaller copy of a model so it uses less memory when running locally.',
		requiresInput: false,
		inputLabel: 'No file needed',
		inputHelp: 'Choose the model under Advanced settings. This job does not need an upload.',
		prerequisite: 'A model available from Hugging Face or on the Mac Studio.',
		output: 'A folder containing the smaller model.',
		nextStep: 'Download the model folder and load it with MLX.',
		duration: 'Up to a few hours.'
	}
];

export const JOB_TYPE_FIELDS: Record<JobType, JobTypeFieldDef[]> = {
	'prompt-gen': [],
	'teacher-gen': [
		{
			name: 'MODEL_PATH',
			label: 'Teacher model',
			type: 'text',
			default: 'mlx-community/Meta-Llama-3.1-8B-Instruct-4bit',
			help: 'The Hugging Face name or local folder for the model that will answer the prompts.'
		},
		{
			name: 'MAX_TOKENS',
			label: 'Maximum answer length',
			type: 'number',
			default: 512,
			help: 'The maximum number of tokens in each answer. The model may stop sooner.'
		},
		{
			name: 'TEMPERATURE',
			label: 'Answer variety',
			type: 'number',
			default: 0.7,
			step: 0.1,
			help: 'Lower values are more predictable. Higher values produce more varied answers.'
		}
	],
	finetune: [
		{
			name: 'MODEL_PATH',
			label: 'Model to train',
			type: 'text',
			default: 'mlx-community/Meta-Llama-3.1-8B-Instruct-4bit',
			help: 'The Hugging Face name or local folder for the starting model.'
		},
		{ name: 'ADAPTER_PATH', label: 'Output folder name', type: 'text', default: 'adapters', help: 'Folder name used inside this job\'s downloaded result.' },
		{ name: 'ITERS', label: 'Training steps', type: 'number', default: 1000, help: 'How many times the trainer updates the adapter.' },
		{ name: 'BATCH_SIZE', label: 'Examples per step', type: 'number', default: 4, help: 'Higher values use more memory. Keep the default unless your instructor recommends a change.' },
		{ name: 'LEARNING_RATE', label: 'Learning rate', type: 'number', default: 0.00001, step: 0.000001, help: 'Controls how much the adapter changes at each training step.' }
	],
	distill: [
		{
			name: 'TEACHER_MODEL_PATH',
			label: 'Teacher model',
			type: 'text',
			default: 'mlx-community/Meta-Llama-3.1-8B-Instruct',
			help: 'The larger reference model used during training.'
		},
		{
			name: 'MODEL_PATH',
			label: 'Student model',
			type: 'text',
			default: 'mlx-community/Meta-Llama-3.1-8B-Instruct-4bit',
			help: 'The smaller model that will receive the adapter. It must use the same tokenizer as the teacher.'
		},
		{ name: 'ADAPTER_PATH', label: 'Output folder name', type: 'text', default: 'adapters', help: 'Folder name used inside this job\'s downloaded result.' },
		{ name: 'ITERS', label: 'Training steps', type: 'number', default: 1000, help: 'How many times the trainer updates the adapter.' },
		{ name: 'BATCH_SIZE', label: 'Examples per step', type: 'number', default: 4, help: 'Higher values use more memory. Keep the default unless your instructor recommends a change.' },
		{ name: 'LEARNING_RATE', label: 'Learning rate', type: 'number', default: 0.00001, step: 0.000001, help: 'Controls how much the adapter changes at each training step.' },
		{ name: 'TEMPERATURE', label: 'Distillation temperature', type: 'number', default: 2.0, step: 0.1, help: 'Controls how much detail the student learns from the teacher\'s probability scores.' },
		{
			name: 'ALPHA',
			label: 'Teacher probability weight',
			type: 'number',
			default: 0.5,
			step: 0.1,
			help: 'Use 0 for answer-only training and 1 for probability-only training. Experimental comparisons should include a run set to 0.'
		},
		{ name: 'LORA_RANK', label: 'Adapter rank', type: 'number', default: 8, help: 'Controls adapter capacity and memory use.' },
		{ name: 'LORA_LAYERS', label: 'Model layers to train', type: 'number', default: 16, help: 'The trainer updates this many layers from the end of the model.' }
	],
	quantize: [
		{ name: 'MODEL_PATH', label: 'Model to make smaller', type: 'text', default: 'mlx-community/Meta-Llama-3.1-8B-Instruct', help: 'The Hugging Face name or local folder for the source model.' },
		{ name: 'Q_BITS', label: 'Bits per weight', type: 'number', default: 4, help: 'Fewer bits make a smaller model but may reduce quality.' },
		{ name: 'Q_GROUP_SIZE', label: 'Quantization group size', type: 'number', default: 64, help: 'How many weights share one scaling value. Keep the default unless you are running a controlled comparison.' }
	]
};

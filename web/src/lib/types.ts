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
	badge: string;
	badgeTone: 'neutral' | 'recommended' | 'advanced';
	requiresInput: boolean;
	inputLabel: string;
	inputHelp: string;
	inputExample?: string;
	exampleDownload?: string;
	prerequisite: string;
	output: string;
	nextStep: string;
	nextActions: { jobType: JobType; label: string; description: string }[];
	duration: string;
	warning?: string;
}

export const GLOSSARY_TERMS = [
	{
		term: 'Model',
		definition: 'Software trained to read and write text. A model name identifies the exact version Moonshine will use.'
	},
	{
		term: 'JSONL',
		definition: 'A text file with one JSON object on each line. Moonshine treats every line as a separate topic, prompt, or training example.'
	},
	{
		term: 'Prompt',
		definition: 'A question or instruction sent to a model.'
	},
	{
		term: 'Teacher model',
		definition: 'The model that creates example answers for another model to learn from.'
	},
	{
		term: 'Student model',
		definition: 'The model being trained. It is usually smaller than the teacher model.'
	},
	{
		term: 'Fine-tuning',
		definition: 'Teaching an existing model from prompt-and-answer examples.'
	},
	{
		term: 'Distillation',
		definition: 'Training a student model from both the teacher\'s answers and its scores for possible next tokens.'
	},
	{
		term: 'LoRA adapter',
		definition: 'A small set of files that stores new training. It is used together with the original model, not by itself.'
	},
	{
		term: 'Token',
		definition: 'A short piece of text processed by a model. A word may contain one or more tokens.'
	},
	{
		term: 'Token probabilities',
		definition: 'The teacher model\'s scores for which token should come next. Distillation uses these scores during training.'
	},
	{
		term: 'Tokenizer',
		definition: 'The rules a model uses to split text into tokens. Distillation only works when both models use the same rules.'
	},
	{
		term: 'Distillation temperature',
		definition: 'Controls how spread out the teacher model\'s token scores are during training. Keep the default unless your experiment requires another value.'
	},
	{
		term: 'Quantization',
		definition: 'Storing model weights with fewer bits so the model uses less memory. Using fewer bits can reduce answer quality.'
	},
	{
		term: 'Hugging Face model name',
		definition: 'The public identifier for a model, such as mlx-community/Meta-Llama-3.1-8B-Instruct-4bit.'
	},
	{
		term: 'MLX',
		definition: 'The software Moonshine uses to run and train models on Apple Silicon Macs.'
	}
] as const;

export const JOB_TYPES: JobTypeDef[] = [
	{
		value: 'prompt-gen',
		label: 'Generate prompts',
		description: 'Turn a list of topics into questions for a teacher model.',
		badge: 'Optional first step',
		badgeTone: 'neutral',
		requiresInput: true,
		inputLabel: 'Topic file',
		inputHelp: 'Upload a JSONL file with one topic per line. The n value is how many questions to make for that topic.',
		inputExample: '{"topic":"robotics","n":5}',
		exampleDownload: '/examples/topics.jsonl',
		prerequisite: 'None. This is usually the first step.',
		output: 'A JSONL file containing generated prompts.',
		nextStep: 'Use the downloaded file as the input for a Teacher answers job.',
		nextActions: [
			{
				jobType: 'teacher-gen',
				label: 'Continue to teacher answers',
				description: 'Download this result first, then upload it as the prompt file.'
			}
		],
		duration: 'Usually a few minutes.',
		warning: 'This starter currently makes generic questions from each topic. Use it for project data only after your instructor confirms it is ready.'
	},
	{
		value: 'teacher-gen',
		label: 'Generate teacher answers',
		description: 'Have a model answer every prompt in your file.',
		badge: 'Step 2',
		badgeTone: 'neutral',
		requiresInput: true,
		inputLabel: 'Prompt file',
		inputHelp: 'Upload a JSONL file with one prompt per line.',
		inputExample: '{"prompt":"Explain how a gear ratio changes torque."}',
		exampleDownload: '/examples/prompts.jsonl',
		prerequisite: 'A prompt file you created yourself or downloaded from Generate prompts.',
		output: 'A JSONL file containing each prompt and the teacher model\'s answer.',
		nextStep: 'Use the downloaded file to Fine-tune a model or run a Distillation experiment.',
		nextActions: [
			{
				jobType: 'finetune',
				label: 'Fine-tune a model',
				description: 'Recommended. Train from the saved prompt-and-answer examples.'
			},
			{
				jobType: 'distill',
				label: 'Run a distillation experiment',
				description: 'Advanced. Compare teacher and student models during training.'
			}
		],
		duration: 'A few minutes to several hours, depending on the number of prompts.'
	},
	{
		value: 'finetune',
		label: 'Fine-tune a model',
		description: 'Teach a model from saved prompt-and-answer examples. Recommended for most teams.',
		badge: 'Step 3, recommended',
		badgeTone: 'recommended',
		requiresInput: true,
		inputLabel: 'Training file',
		inputHelp: 'Upload the JSONL result from a completed Generate teacher answers job.',
		inputExample: '{"prompt":"What is torque?","completion":"Torque is a turning force."}',
		exampleDownload: '/examples/training.jsonl',
		prerequisite: 'A completed Generate teacher answers job.',
		output: 'A LoRA adapter, a small set of files that stores the new training.',
		nextStep: 'Download the adapter. Use it together with the original model when testing.',
		nextActions: [],
		duration: 'Up to half a day.'
	},
	{
		value: 'distill',
		label: 'Run a distillation experiment',
		description: 'Compare a teacher and student model during training. Intended for advanced experiments.',
		badge: 'Advanced alternative',
		badgeTone: 'advanced',
		requiresInput: true,
		inputLabel: 'Training file',
		inputHelp: 'Upload the JSONL result from a completed Generate teacher answers job.',
		inputExample: '{"prompt":"What is torque?","completion":"Torque is a turning force."}',
		exampleDownload: '/examples/training.jsonl',
		prerequisite: 'A completed Generate teacher answers job and compatible teacher and student models.',
		output: 'A LoRA adapter trained from answers plus the teacher model\'s scores for possible next tokens.',
		nextStep: 'Download the adapter and compare it with a Fine-tune job that used the same data.',
		nextActions: [
			{
				jobType: 'finetune',
				label: 'Run the recommended comparison',
				description: 'Use the same training file in a Fine-tune job, then compare the two adapters.'
			}
		],
		duration: 'Up to half a day.',
		warning: 'Both models must split text into tokens in exactly the same way. This is called using the same tokenizer. Ask your instructor before changing either model.'
	},
	{
		value: 'quantize',
		label: 'Make a model smaller',
		description: 'Create a smaller copy of a model so it uses less memory when running locally.',
		badge: 'Separate tool',
		badgeTone: 'advanced',
		requiresInput: false,
		inputLabel: 'No file needed',
		inputHelp: 'Choose the model under Advanced settings. This job does not need an upload.',
		prerequisite: 'A model available from Hugging Face or on the Mac Studio.',
		output: 'A folder containing the smaller model.',
		nextStep: 'Download the model folder. MLX is the software used to run it on an Apple Silicon Mac.',
		nextActions: [],
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

import { z } from 'zod';

const progressSchema = z.object({
	messages: z.array(z.object({ role: z.enum(['user', 'assistant']), content: z.string() })),
	input: z.string(),
	prompt: z.string()
});

export type InterviewProgress = z.infer<typeof progressSchema>;
export type InterviewMessage = InterviewProgress['messages'][number];

function storageKey(name: string): string {
	return `moonshine.interview.v2:${encodeURIComponent(name)}`;
}

export function loadInterviewProgress(name: string): InterviewProgress {
	const saved = localStorage.getItem(storageKey(name));
	if (saved !== null) return progressSchema.parse(JSON.parse(saved));
	// Carry forward an interview from the current tab's previous storage format.
	const progress = progressSchema.parse({
		messages: JSON.parse(sessionStorage.getItem('moonshine.interview.v1') ?? '[]'),
		input: '',
		prompt: sessionStorage.getItem('moonshine.prompt.v1') ?? ''
	});
	if (progress.messages.length || progress.prompt) saveInterviewProgress(name, progress);
	return progress;
}

export function saveInterviewProgress(name: string, progress: InterviewProgress): void {
	localStorage.setItem(storageKey(name), JSON.stringify(progress));
	sessionStorage.removeItem('moonshine.interview.v1');
	sessionStorage.removeItem('moonshine.prompt.v1');
}

import META_PROMPT from '../prompts/interview-meta-prompt.md?raw';
import { teacherChat, type ChatMessage } from './ollama';

/**
 * Generate Prompt, both paths use the SAME meta-prompt text
 * (src/lib/prompts/interview-meta-prompt.md):
 *   Path 1: in-app interview with the teacher -> runInterviewTurn()
 *   Path 2: download the meta-prompt -> getMetaPromptDownload()
 */
export const INTERVIEW_META_PROMPT: string = META_PROMPT;

export const PROMPT_START = '=== PROMPT';
export const PROMPT_END = '=== END PROMPT ===';

export type InterviewMessage = { role: 'user' | 'assistant'; content: string };

export interface InterviewTurn {
	/** The teacher's chat reply (shown in the chat). */
	reply: string;
	/** Set when the reply contains the final PROMPT (between the markers). */
	prompt: string | null;
}

const MAX_TURNS = 60;
const MAX_CHARS = 30000;

export function validateInterviewMessages(messages: unknown): messages is InterviewMessage[] {
	return (
		Array.isArray(messages) &&
		messages.length <= MAX_TURNS &&
		messages.every(
			(m) =>
				m &&
				typeof m === 'object' &&
				((m as InterviewMessage).role === 'user' || (m as InterviewMessage).role === 'assistant') &&
				typeof (m as InterviewMessage).content === 'string' &&
				(m as InterviewMessage).content.length <= MAX_CHARS
		) &&
		(messages.length === 0 || messages.at(-1)?.role === 'user')
	);
}

/** Pull the final PROMPT out of a teacher reply, if present. */
export function extractPrompt(reply: string): string | null {
	const start = reply.indexOf(PROMPT_START);
	if (start === -1) return null;
	const afterHeader = reply.indexOf('\n', start);
	if (afterHeader === -1) return null;
	const end = reply.indexOf(PROMPT_END, afterHeader);
	const body = reply.slice(afterHeader + 1, end === -1 ? undefined : end).trim();
	return body.length > 200 ? body : null;
}

/**
 * One interview turn. Pass the whole visible conversation (may be empty to get
 * the greeting). Returns the teacher reply and, when finished, the PROMPT.
 */
export async function runInterviewTurn(messages: InterviewMessage[]): Promise<InterviewTurn> {
	if (!validateInterviewMessages(messages)) throw new Error('Invalid interview conversation');
	const convo: ChatMessage[] = [{ role: 'system', content: INTERVIEW_META_PROMPT }];
	if (messages.length === 0) convo.push({ role: 'user', content: 'Hi! I am ready to start.' });
	convo.push(...messages);
	const { content } = await teacherChat(convo, { temperature: 0.6, maxTokens: 6000, timeoutMs: 600_000 });
	return { reply: content, prompt: extractPrompt(content) };
}

/** Path 2: the meta-prompt as a downloadable file. */
export function getMetaPromptDownload(format: 'md' | 'txt' = 'md'): {
	filename: string;
	contentType: string;
	body: string;
} {
	return {
		filename: `moonshine-prompt-coach.${format}`,
		contentType: format === 'md' ? 'text/markdown; charset=utf-8' : 'text/plain; charset=utf-8',
		body: INTERVIEW_META_PROMPT
	};
}

/** The finished PROMPT as a download (.md or .txt), clearly labeled as a PROMPT. */
export function getPromptDownload(
	prompt: string,
	format: 'md' | 'txt' = 'md',
	ownerName?: string
): { filename: string; contentType: string; body: string } {
	const slug = (ownerName ?? 'my').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'my';
	const body = prompt.trimStart().startsWith('PROMPT') ? prompt.trim() + '\n' : `PROMPT\n\n${prompt.trim()}\n`;
	return {
		filename: `${slug}-PROMPT.${format}`,
		contentType: format === 'md' ? 'text/markdown; charset=utf-8' : 'text/plain; charset=utf-8',
		body
	};
}

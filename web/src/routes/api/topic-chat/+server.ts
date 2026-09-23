import { json, type RequestHandler } from '@sveltejs/kit';
import { generateText, Output } from 'ai';
import { topicModel, topicModelError } from '$lib/server/topic-model';
import { z } from 'zod';

type Message = { role: 'user' | 'assistant'; content: string };

const SYSTEM_PROMPT = `You are a requirements interviewer helping a student team design fine-tuning data. Your job is to draw out concrete decisions from the user, then turn those decisions into a reusable prompt for a separate model that generates question-and-answer pairs. The conversation's artifact is the GENERATION PROMPT, not the Q&A pairs themselves.

Interview rules:
- Identify the single most consequential missing or vague detail on every turn and ask one focused follow-up question about it.
- Do not silently fill important gaps with assumptions. Name the ambiguity and make the user choose or explain. Offer 2–4 concrete examples or options when that makes the question easier to answer.
- Press for observable specifics: what the trained model should know or do, who asks the questions, real situations it must handle, boundaries and exclusions, desired difficulty, answer format and tone, authoritative sources, dangerous misconceptions, and what a bad answer looks like.
- Follow an answer with a deeper question when it remains broad. Challenge contradictions and requests that would produce unreliable or unfocused training data.
- Keep the interview conversational and concise. Never ask a list of questions in one turn.

Prompt rules:
- Maintain a concrete, self-contained draft generation prompt from the user's confirmed decisions. Revise it every turn, but mark unresolved choices as explicit placeholders rather than inventing answers.
- Tell the generator to return a JSON array of objects with "question" and "answer" strings, avoid unsupported claims, and create varied examples within scope.
- The "message" should briefly reflect what you learned, explain any meaningful prompt revision, and end with the next focused interview question.
- Return the complete revised generation prompt every turn. Return JSON only.`;

const responseSchema = z.object({
	message: z.string(),
	generationPrompt: z.string()
});

export const POST: RequestHandler = async ({ request, locals }) => {
	if (!locals.user) return json({ error: 'Please sign in.' }, { status: 401 });
	let body: unknown;
	try {
		body = await request.json();
	} catch {
		return json({ error: 'Invalid request.' }, { status: 400 });
	}
	if (!body || typeof body !== 'object') return json({ error: 'Invalid request.' }, { status: 400 });
	const { messages, generationPrompt } = body as Record<string, unknown>;
	if (
		!Array.isArray(messages) || messages.length < 1 || messages.length > 20 ||
		!messages.every((item): item is Message =>
			item && typeof item === 'object' &&
			(item.role === 'user' || item.role === 'assistant') &&
			typeof item.content === 'string' && item.content.length <= 4000
		) ||
		messages.at(-1)?.role !== 'user' ||
		typeof generationPrompt !== 'string' || generationPrompt.length > 6000
	) return json({ error: 'The conversation is too long or contains invalid content.' }, { status: 400 });

	try {
		const { output: draft } = await generateText({
			model: topicModel(request),
			system: `${SYSTEM_PROMPT}\nCurrent generation prompt: ${JSON.stringify(generationPrompt)}`,
			messages,
			output: Output.object({ schema: responseSchema }),
			temperature: 0.5,
			maxOutputTokens: 1800,
			abortSignal: AbortSignal.timeout(120_000)
		});
		return json({
			message: draft.message.slice(0, 4000),
			generationPrompt: draft.generationPrompt.slice(0, 6000)
		});
	} catch (error) {
		console.error('Topic chat failed:', error);
		return json({ error: topicModelError(request) }, { status: 503 });
	}
};

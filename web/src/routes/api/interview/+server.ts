import { json, type RequestHandler } from '@sveltejs/kit';
import { runInterviewTurn, validateInterviewMessages } from '$lib/server/interview';

/** POST {messages: [{role, content}...]} -> {reply, prompt|null}. Empty messages = greeting. */
export const POST: RequestHandler = async ({ request, locals }) => {
	if (!locals.user) return json({ error: 'Please sign in.' }, { status: 401 });
	const body = (await request.json().catch(() => null)) as { messages?: unknown } | null;
	const messages = body?.messages ?? [];
	if (!validateInterviewMessages(messages))
		return json({ error: 'The conversation is too long or contains invalid content.' }, { status: 400 });
	try {
		return json(await runInterviewTurn(messages));
	} catch (error) {
		console.error('Interview turn failed:', error);
		return json({ error: 'The teacher model is unavailable. Ask an instructor to check Ollama.' }, { status: 503 });
	}
};

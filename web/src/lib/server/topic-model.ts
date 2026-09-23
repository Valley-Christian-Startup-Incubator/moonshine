import { createOpenAICompatible } from '@ai-sdk/openai-compatible';
import { createOpenAI } from '@ai-sdk/openai';
import { createOpenAIOAuth } from '@openai-oauth/ai-sdk';
import { openaiCredentials } from '@openai-oauth/web/server';

export function topicModel(request: Request) {
	if (request.headers.has('authorization') && request.headers.has('chatgpt-account-id')) {
		return createOpenAIOAuth(openaiCredentials(request))('gpt-5.6-terra');
	}
	if (process.env.OPENAI_API_KEY) {
		return createOpenAI({ apiKey: process.env.OPENAI_API_KEY })(process.env.TOPIC_CHAT_MODEL ?? 'gpt-6-astra');
	}
	const url = process.env.DIAGNOSTIC_OLLAMA_URL ?? 'http://127.0.0.1:11434';
	return createOpenAICompatible({ name: 'ollama', baseURL: `${url.replace(/\/$/, '')}/v1` })(
		process.env.TOPIC_CHAT_MODEL ?? process.env.DIAGNOSTIC_MODEL ?? 'qwen3.8:27b-mlx'
	);
}

export function topicModelError(request: Request): string {
	if (request.headers.has('authorization')) return 'The ChatGPT request failed. Try signing in again.';
	if (process.env.OPENAI_API_KEY) return 'The GPT request failed. Ask an instructor to check the server API key and model.';
	return 'The local model is unavailable. Ask an instructor to check Ollama and its configured model.';
}

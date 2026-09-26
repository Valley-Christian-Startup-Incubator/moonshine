import { error, type RequestHandler } from '@sveltejs/kit';
import { getPromptDownload } from '$lib/server/interview';

/** POST form or JSON {prompt, format: md|txt} -> the finished PROMPT as a download. */
export const POST: RequestHandler = async ({ request, locals }) => {
	let prompt: unknown;
	let format: unknown;
	if ((request.headers.get('content-type') ?? '').includes('application/json')) {
		const b = (await request.json().catch(() => ({}))) as Record<string, unknown>;
		prompt = b.prompt;
		format = b.format;
	} else {
		const f = await request.formData();
		prompt = f.get('prompt');
		format = f.get('format');
	}
	if (typeof prompt !== 'string' || prompt.trim().length < 20 || prompt.length > 100_000) error(400, 'No PROMPT to download.');
	const file = getPromptDownload(prompt, format === 'txt' ? 'txt' : 'md', locals.userName ?? undefined);
	return new Response(file.body, {
		headers: { 'Content-Type': file.contentType, 'Content-Disposition': `attachment; filename="${file.filename}"` }
	});
};

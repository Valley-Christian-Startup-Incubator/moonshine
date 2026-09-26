import type { RequestHandler } from '@sveltejs/kit';
import { getMetaPromptDownload } from '$lib/server/interview';

/** GET ?format=md|txt -> the interview meta-prompt as a file (Generate Prompt, path 2). */
export const GET: RequestHandler = async ({ url }) => {
	const f = getMetaPromptDownload(url.searchParams.get('format') === 'txt' ? 'txt' : 'md');
	return new Response(f.body, {
		headers: { 'Content-Type': f.contentType, 'Content-Disposition': `attachment; filename="${f.filename}"` }
	});
};

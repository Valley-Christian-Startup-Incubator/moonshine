import { open } from 'node:fs/promises';

export interface PreviewRow {
	raw: string;
	value: unknown;
	invalid: boolean;
}

const MAX_BYTES = 1024 * 1024;
const PAGE_ROWS = 20;

/** Byte cursors keep reads bounded even when previewing large datasets. */
export async function readJsonlPreview(filePath: string, offset = 0) {
	if (!Number.isSafeInteger(offset) || offset < 0) throw new RangeError('Invalid preview position');
	const file = await open(filePath, 'r');
	try {
		const { size } = await file.stat();
		if (offset > size) throw new RangeError('The file has changed. Reopen the preview.');
		if (offset > 0) {
			const previous = Buffer.alloc(1);
			await file.read(previous, 0, 1, offset - 1);
			if (previous[0] !== 10) throw new RangeError('Invalid preview position');
		}
		const buffer = Buffer.alloc(Math.min(MAX_BYTES, size - offset));
		const { bytesRead } = await file.read(buffer, 0, buffer.length, offset);
		const rows: PreviewRow[] = [];
		let consumed = 0;
		while (consumed < bytesRead && rows.length < PAGE_ROWS) {
			const newline = buffer.indexOf(10, consumed);
			if (newline < 0 && offset + bytesRead < size) break;
			const end = newline < 0 ? bytesRead : newline;
			const raw = buffer.subarray(consumed, end).toString('utf8').replace(/^\uFEFF/, '').trim();
			consumed = newline < 0 ? bytesRead : end + 1;
			if (!raw) continue;
			try {
				rows.push({ raw, value: JSON.parse(raw), invalid: false });
			} catch {
				rows.push({ raw, value: null, invalid: true });
			}
		}
		if (!consumed && bytesRead) throw new RangeError('This entry is too large to preview (over 1 MB). Download the file to read it.');
		return { rows, nextOffset: offset + consumed < size ? offset + consumed : null };
	} finally {
		await file.close();
	}
}

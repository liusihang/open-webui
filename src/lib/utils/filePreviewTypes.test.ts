import { describe, expect, it } from 'vitest';
import { isOnlyOfficePreviewFile } from './filePreviewTypes';

describe('OnlyOffice file preview selection', () => {
	it.each(['demo.pdf', 'demo.docx', 'demo.DOC', 'demo.xlsx', 'demo.pptx', 'demo.odt', 'demo.csv'])(
		'offers optional OnlyOffice for %s',
		(name) => expect(isOnlyOfficePreviewFile({ name })).toBe(true)
	);
	it.each([
		'demo.txt',
		'demo.md',
		'demo.json',
		'demo.py',
		'demo.html',
		'demo.png',
		'demo.mp3',
		'demo.mp4'
	])('keeps native preview for %s', (name) =>
		expect(isOnlyOfficePreviewFile({ name })).toBe(false)
	);
	it('recognizes office MIME types and retains text/image precedence', () => {
		expect(
			isOnlyOfficePreviewFile({
				filename: 'unknown',
				meta: { content_type: 'application/pdf; charset=utf-8' }
			})
		).toBe(true);
		expect(
			isOnlyOfficePreviewFile({ name: 'source.txt', meta: { content_type: 'application/pdf' } })
		).toBe(false);
		expect(isOnlyOfficePreviewFile({ meta: { content_type: 'image/png' } })).toBe(false);
		expect(isOnlyOfficePreviewFile(null)).toBe(false);
	});
});

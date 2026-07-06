import { describe, expect, it } from 'vitest';

import { isOnlyOfficePreviewFile } from './filePreviewTypes';

describe('file preview type routing', () => {
	it.each([
		'pdf',
		'docx',
		'docm',
		'dotx',
		'odt',
		'ott',
		'rtf',
		'xlsx',
		'xlsm',
		'xlsb',
		'xltx',
		'ods',
		'ots',
		'csv',
		'pptx',
		'pptm',
		'ppsx',
		'potx',
		'odp',
		'otp'
	])(
		'routes %s files to OnlyOffice',
		(ext) => {
			expect(isOnlyOfficePreviewFile({ name: `sample.${ext}` })).toBe(true);
		}
	);

	it.each(['txt', 'md', 'json', 'html', 'js', 'png', 'mp4', 'zip', 'bin'])(
		'does not route native or unsupported %s files to OnlyOffice',
		(ext) => {
			expect(isOnlyOfficePreviewFile({ name: `sample.${ext}` })).toBe(false);
		}
	);

	it('uses office MIME types when an upload has no useful extension', () => {
		expect(
			isOnlyOfficePreviewFile({
				name: 'sample',
				meta: { content_type: 'application/vnd.oasis.opendocument.text' }
			})
		).toBe(true);
		expect(
			isOnlyOfficePreviewFile({
				name: 'sample',
				meta: { content_type: 'application/vnd.oasis.opendocument.spreadsheet' }
			})
		).toBe(true);
		expect(
			isOnlyOfficePreviewFile({
				name: 'sample',
				meta: { content_type: 'application/vnd.oasis.opendocument.presentation' }
			})
		).toBe(true);
	});
});

type FilePreviewLike = {
	name?: string | null;
	filename?: string | null;
	meta?: {
		name?: string | null;
		content_type?: string | null;
	};
};

export const ONLYOFFICE_PREVIEW_EXTENSIONS = new Set([
	'pdf',
	'doc',
	'docm',
	'docx',
	'dot',
	'dotm',
	'dotx',
	'odt',
	'ott',
	'rtf',
	'xls',
	'xlsb',
	'xlsm',
	'xlsx',
	'xlt',
	'xltm',
	'xltx',
	'csv',
	'ods',
	'ots',
	'odp',
	'otp',
	'pot',
	'potm',
	'potx',
	'pps',
	'ppsm',
	'ppsx',
	'ppt',
	'pptm',
	'pptx'
]);

const NATIVE_PREVIEW_EXTENSIONS = new Set([
	'txt',
	'text',
	'log',
	'md',
	'markdown',
	'mdx',
	'json',
	'jsonc',
	'jsonl',
	'json5',
	'html',
	'htm',
	'css',
	'xml',
	'yaml',
	'yml',
	'py',
	'js',
	'ts',
	'java',
	'cpp',
	'c',
	'h',
	'sh',
	'bash',
	'sql',
	'go',
	'rs',
	'php',
	'rb',
	'png',
	'jpg',
	'jpeg',
	'gif',
	'webp',
	'svg',
	'bmp',
	'ico',
	'mp3',
	'wav',
	'ogg',
	'm4a',
	'webm',
	'mp4',
	'mov',
	'avi',
	'mkv',
	'm4v'
]);

const ONLYOFFICE_PREVIEW_MIME_TYPES = new Set([
	'application/pdf',
	'application/msword',
	'application/rtf',
	'application/vnd.ms-word.document.macroenabled.12',
	'application/vnd.ms-word.template.macroenabled.12',
	'application/vnd.oasis.opendocument.text',
	'application/vnd.oasis.opendocument.text-template',
	'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
	'application/vnd.openxmlformats-officedocument.wordprocessingml.template',
	'text/rtf',
	'application/csv',
	'application/vnd.ms-excel',
	'application/vnd.ms-excel.sheet.binary.macroenabled.12',
	'application/vnd.ms-excel.sheet.macroenabled.12',
	'application/vnd.ms-excel.template.macroenabled.12',
	'application/vnd.oasis.opendocument.spreadsheet',
	'application/vnd.oasis.opendocument.spreadsheet-template',
	'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
	'application/vnd.openxmlformats-officedocument.spreadsheetml.template',
	'text/csv',
	'application/vnd.ms-powerpoint',
	'application/vnd.ms-powerpoint.presentation.macroenabled.12',
	'application/vnd.ms-powerpoint.slideshow.macroenabled.12',
	'application/vnd.ms-powerpoint.template.macroenabled.12',
	'application/vnd.oasis.opendocument.presentation',
	'application/vnd.oasis.opendocument.presentation-template',
	'application/vnd.openxmlformats-officedocument.presentationml.presentation',
	'application/vnd.openxmlformats-officedocument.presentationml.slideshow',
	'application/vnd.openxmlformats-officedocument.presentationml.template'
]);

const NATIVE_PREVIEW_MIME_TYPES = new Set([
	'application/json',
	'application/javascript',
	'application/x-javascript',
	'application/xml',
	'text/html',
	'text/javascript',
	'text/markdown',
	'text/plain',
	'text/xml'
]);

const getPreviewName = (file?: FilePreviewLike | null) =>
	file?.name ?? file?.filename ?? file?.meta?.name ?? '';

export const getFileExtension = (fileName?: string | null) => {
	const name = (fileName ?? '').trim();
	const baseName = name.split(/[\\/]/).pop() ?? '';
	const extension = baseName.includes('.') ? baseName.split('.').pop() : '';
	return extension?.toLowerCase() ?? '';
};

export const isOnlyOfficePreviewFile = (file?: FilePreviewLike | null) => {
	const extension = getFileExtension(getPreviewName(file));
	if (NATIVE_PREVIEW_EXTENSIONS.has(extension)) {
		return false;
	}
	if (ONLYOFFICE_PREVIEW_EXTENSIONS.has(extension)) {
		return true;
	}

	const contentType = (file?.meta?.content_type ?? '').split(';')[0].trim().toLowerCase();
	if (!contentType) {
		return false;
	}
	if (
		contentType.startsWith('image/') ||
		contentType.startsWith('audio/') ||
		contentType.startsWith('video/') ||
		NATIVE_PREVIEW_MIME_TYPES.has(contentType)
	) {
		return false;
	}

	return ONLYOFFICE_PREVIEW_MIME_TYPES.has(contentType);
};

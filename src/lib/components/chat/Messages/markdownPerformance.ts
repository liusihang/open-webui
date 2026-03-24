export const LARGE_MARKDOWN_PARSE_THRESHOLD = 120_000;
export const LARGE_MARKDOWN_PREVIEW_CHARS = 4_000;

export const shouldDeferMarkdownParsing = (
	content: unknown,
	done = true,
	preview = false
) => {
	return (
		typeof content === 'string' &&
		done &&
		!preview &&
		content.length > LARGE_MARKDOWN_PARSE_THRESHOLD
	);
};

export const getLargeMarkdownPreview = (content: unknown) => {
	if (typeof content !== 'string') {
		return '';
	}

	return content.slice(0, LARGE_MARKDOWN_PREVIEW_CHARS);
};

import { describe, expect, it } from 'vitest';

import {
	LARGE_MARKDOWN_PARSE_THRESHOLD,
	getLargeMarkdownPreview,
	shouldDeferMarkdownParsing
} from './markdownPerformance';


describe('shouldDeferMarkdownParsing', () => {
	it('defers large completed markdown responses', () => {
		expect(shouldDeferMarkdownParsing('x'.repeat(LARGE_MARKDOWN_PARSE_THRESHOLD + 1), true, false)).toBe(
			true
		);
	});

	it('keeps normal or preview content on the eager path', () => {
		expect(shouldDeferMarkdownParsing('small', true, false)).toBe(false);
		expect(shouldDeferMarkdownParsing('x'.repeat(LARGE_MARKDOWN_PARSE_THRESHOLD + 1), false, false)).toBe(
			false
		);
		expect(shouldDeferMarkdownParsing('x'.repeat(LARGE_MARKDOWN_PARSE_THRESHOLD + 1), true, true)).toBe(
			false
		);
	});
});


describe('getLargeMarkdownPreview', () => {
	it('returns a stable preview slice for large content', () => {
		const preview = getLargeMarkdownPreview('abcdef');
		expect(preview).toBe('abcdef');
	});
});

import { describe, expect, it } from 'vitest';

import {
	getTextTokenSegments,
	getTextTokenShouldPreserveStreamingMarkup
} from './textToken';

describe('text token rendering helpers', () => {
	it('preserves words with their trailing whitespace when splitting streaming text', () => {
		expect(getTextTokenSegments('Tool result: hello world')).toEqual([
			'Tool ',
			'result: ',
			'hello ',
			'world'
		]);
	});

	it('keeps standalone whitespace segments intact', () => {
		expect(getTextTokenSegments('hello\n\nworld')).toEqual(['hello\n\n', 'world']);
		expect(getTextTokenSegments('  indented')).toEqual(['  ', 'indented']);
	});

	it('keeps the streaming markup mode after a token has streamed once', () => {
		let preserve = getTextTokenShouldPreserveStreamingMarkup(false, false);
		preserve = getTextTokenShouldPreserveStreamingMarkup(preserve, true);

		expect(preserve).toBe(true);
		expect(getTextTokenShouldPreserveStreamingMarkup(false, true)).toBe(false);
	});
});

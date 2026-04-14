import { describe, expect, it } from 'vitest';
import type { Token } from 'marked';

import { isProgressDetailsToken, selectProgressDetailsState } from './progressDetails';

const detailsToken = (attributes: Record<string, string> = {}, text = 'content'): Token =>
	({
		type: 'details',
		raw: '',
		summary: '',
		text,
		attributes
	}) as unknown as Token;

const paragraphToken = (): Token =>
	({
		type: 'paragraph',
		raw: 'hello',
		text: 'hello',
		tokens: []
	}) as unknown as Token;

describe('isProgressDetailsToken', () => {
	it('accepts reasoning and tool_calls details tokens only', () => {
		expect(isProgressDetailsToken(detailsToken({ type: 'reasoning' }))).toBe(true);
		expect(isProgressDetailsToken(detailsToken({ type: 'tool_calls' }))).toBe(true);
		expect(isProgressDetailsToken(detailsToken({ type: 'code_interpreter' }))).toBe(false);
		expect(isProgressDetailsToken(paragraphToken())).toBe(false);
	});
});

describe('selectProgressDetailsState', () => {
	it('prefers the latest running item as current', () => {
		const firstDone = detailsToken({ type: 'tool_calls', id: 'a', done: 'true' });
		const runningOne = detailsToken({ type: 'tool_calls', id: 'b', done: 'false' });
		const runningTwo = detailsToken({ type: 'reasoning', id: 'c', done: 'false' });

		const selected = selectProgressDetailsState([firstDone, runningOne, runningTwo]);

		expect(selected?.currentToken).toBe(runningTwo);
		expect(selected?.historyTokens).toEqual([firstDone, runningOne]);
	});

	it('falls back to the latest completed item when no running item exists', () => {
		const olderDone = detailsToken({ type: 'reasoning', id: 'a', done: 'true' });
		const latestDone = detailsToken({ type: 'tool_calls', id: 'b', done: 'true' });

		const selected = selectProgressDetailsState([olderDone, latestDone]);

		expect(selected?.currentToken).toBe(latestDone);
		expect(selected?.historyTokens).toEqual([olderDone]);
	});

	it('filters hidden items without hiding the full group', () => {
		const hiddenRunning = detailsToken({
			type: 'tool_calls',
			id: 'hidden-running',
			done: 'false',
			hidden: 'true'
		});
		const visibleDone = detailsToken({ type: 'reasoning', id: 'visible', done: 'true' });

		const selected = selectProgressDetailsState([hiddenRunning, visibleDone]);

		expect(selected?.visibleTokens).toEqual([visibleDone]);
		expect(selected?.currentToken).toBe(visibleDone);
		expect(selected?.historyTokens).toEqual([]);
	});

	it('returns null when all progress items are hidden', () => {
		const hiddenOne = detailsToken({ type: 'tool_calls', id: 'a', hidden: 'true' });
		const hiddenTwo = detailsToken({ type: 'reasoning', id: 'b', hidden: 'true' });

		expect(selectProgressDetailsState([hiddenOne, hiddenTwo])).toBeNull();
	});
});

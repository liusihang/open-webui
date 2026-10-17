import { describe, expect, it } from 'vitest';
import { isAppStateValue, mergeWritingStream, validateIsolatedBlock } from './blocks';

describe('isolated block contracts', () => {
	it('accepts bounded HTML/CSS/worker code and declared keys', () => {
		expect(
			validateIsolatedBlock('AppBlock', {
				title: 'Demo',
				html: '<p>Hi</p>',
				js: 'ui.setText("status", "Ready")',
				stateKeys: ['threshold']
			})
		).toMatchObject({ title: 'Demo' });
	});
	it.each([
		{ stateKeys: ['constructor'] },
		{ stateKeys: ['a', 'a'] },
		{ height: 901 },
		{ js: 'x'.repeat(16385) },
		{ html: 'x'.repeat(24577) },
		{ src: 'https://example.org' }
	])('rejects unsafe or oversized app contract %j', (extra) => {
		expect(() =>
			validateIsolatedBlock('AppBlock', { title: 'Demo', html: '', ...extra })
		).toThrow();
	});
	it('rejects oversized or unsupported writing', () => {
		expect(() =>
			validateIsolatedBlock('WritingBlock', { title: 'Draft', content: 'x'.repeat(16385) })
		).toThrow();
		expect(() =>
			validateIsolatedBlock('WritingBlock', { title: 'Draft', content: '', format: 'html' })
		).toThrow();
	});
	it('bounds messages independently of spec validation', () => {
		for (const value of [Infinity, NaN, 1e13, {}, [], 'x'.repeat(257), null])
			expect(isAppStateValue(value)).toBe(false);
		for (const value of [false, 1, 'East']) expect(isAppStateValue(value)).toBe(true);
	});
	it('preserves edits and appends streamed suffixes', () => {
		expect(mergeWritingStream('Hello', 'Hello world', 'Hi')).toEqual({
			text: 'Hi world',
			conflict: false
		});
		expect(mergeWritingStream('Hello', 'Replacement', 'Hi')).toEqual({
			text: 'Hi',
			conflict: true
		});
		expect(mergeWritingStream('Hello', 'Replacement', 'Hello')).toEqual({
			text: 'Replacement',
			conflict: false
		});
	});
});

import { describe, it, expect } from 'vitest';
import { mergeInteractionState } from './state-value';
describe('interaction metadata budget', () => {
	it('preserves previous blocks and multiline drafts', () => {
		const state = mergeInteractionState({ a: { 'shared:x': 1 } }, 'b', {
			'writing:doc': '文章\n'.repeat(1000)
		});
		expect(state.blocks.a['shared:x']).toBe(1);
		expect(state.blocks.b['writing:doc']).toContain('\n');
	});
	it('rejects aggregate UTF-8 and key budgets without silent truncation', () => {
		const blocks = Object.fromEntries(
			[1, 2].map((n) => ['b' + n, { 'writing:doc': '字'.repeat(16000) }])
		);
		expect(() =>
			mergeInteractionState(blocks, 'b3', { 'writing:doc': '字'.repeat(16000) })
		).toThrow();
		expect(() =>
			mergeInteractionState(
				{},
				'b',
				Object.fromEntries(Array.from({ length: 65 }, (_, i) => ['shared:s' + i, 0]))
			)
		).toThrow();
		expect(() => mergeInteractionState({}, 'b', { 'writing:doc': 'x'.repeat(16385) })).toThrow();
	});
});

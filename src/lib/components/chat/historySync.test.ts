import { describe, expect, it } from 'vitest';

import { mergeHistorySnapshot, mergeServerMessage } from './historySync';

describe('mergeServerMessage', () => {
	it('keeps existing content when the server snapshot has empty content', () => {
		const merged = mergeServerMessage(
			{ id: 'a', content: 'partial answer', role: 'assistant' },
			{ id: 'a', content: '', role: 'assistant', done: true }
		);

		expect(merged.content).toBe('partial answer');
		expect(merged.done).toBe(true);
	});
});

describe('mergeHistorySnapshot', () => {
	it('detects assistant progress from a longer persisted snapshot', () => {
		const currentHistory = {
			currentId: 'a',
			messages: {
				a: { id: 'a', role: 'assistant', content: 'partial', done: false }
			}
		};

		const latestHistory = {
			currentId: 'a',
			messages: {
				a: { id: 'a', role: 'assistant', content: 'partial answer', done: false }
			}
		};

		const result = mergeHistorySnapshot(currentHistory, latestHistory);

		expect(result.history.messages.a.content).toBe('partial answer');
		expect(result.hasAssistantProgress).toBe(true);
		expect(result.hasRenderableAssistantUpdate).toBe(false);
		expect(result.changed).toBe(true);
	});

	it('detects final assistant snapshots so stalled streams can recover', () => {
		const currentHistory = {
			currentId: 'a',
			messages: {
				a: { id: 'a', role: 'assistant', content: 'partial', done: false }
			}
		};

		const latestHistory = {
			currentId: 'a',
			messages: {
				a: { id: 'a', role: 'assistant', content: 'complete answer', done: true }
			}
		};

		const result = mergeHistorySnapshot(currentHistory, latestHistory);

		expect(result.history.messages.a.content).toBe('complete answer');
		expect(result.history.messages.a.done).toBe(true);
		expect(result.hasAssistantProgress).toBe(true);
		expect(result.hasRenderableAssistantUpdate).toBe(true);
	});
});

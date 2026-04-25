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

	it('does not roll back streaming assistant content to a shorter snapshot', () => {
		const merged = mergeServerMessage(
			{ id: 'a', content: 'longer partial answer', role: 'assistant', done: false },
			{ id: 'a', content: 'short', role: 'assistant', done: false }
		);

		expect(merged.content).toBe('longer partial answer');
	});

	it('keeps local status history when server snapshot status history is empty', () => {
		const existingStatusHistory = [{ action: 'tool', description: 'Running', done: false }];
		const merged = mergeServerMessage(
			{ id: 'a', statusHistory: existingStatusHistory },
			{ id: 'a', statusHistory: [] }
		);

		expect(merged.statusHistory).toEqual(existingStatusHistory);
	});

	it('does not roll back to a shorter server status history snapshot', () => {
		const existingStatusHistory = [
			{ action: 'tool', description: 'Searching', done: false },
			{ action: 'tool', description: 'Done', done: true }
		];
		const incomingStatusHistory = [{ action: 'tool', description: 'Done', done: true }];
		const merged = mergeServerMessage(
			{ id: 'a', statusHistory: existingStatusHistory },
			{ id: 'a', statusHistory: incomingStatusHistory }
		);

		expect(merged.statusHistory).toEqual(existingStatusHistory);
	});

	it('keeps prior sources when the incoming replay snapshot is shorter', () => {
		const existingSources = [
			{ source: { id: 's1', name: 'Source 1' } },
			{ source: { id: 's2', name: 'Source 2' } }
		];
		const merged = mergeServerMessage(
			{ id: 'a', sources: existingSources },
			{ id: 'a', sources: [{ source: { id: 's1', name: 'Source 1' } }] }
		);

		expect(merged.sources).toEqual(existingSources);
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

	it('marks snapshot as changed when status history changes without content changes', () => {
		const currentHistory = {
			currentId: 'a',
			messages: {
				a: {
					id: 'a',
					role: 'assistant',
					content: 'same content',
					done: false,
					statusHistory: [{ action: 'tool', description: 'Running', done: false }]
				}
			}
		};

		const latestHistory = {
			currentId: 'a',
			messages: {
				a: {
					id: 'a',
					role: 'assistant',
					content: 'same content',
					done: false,
					statusHistory: [
						{ action: 'tool', description: 'Running', done: false },
						{ action: 'tool', description: 'Done', done: true }
					]
				}
			}
		};

		const result = mergeHistorySnapshot(currentHistory, latestHistory);

		expect(result.history.messages.a.statusHistory).toEqual(latestHistory.messages.a.statusHistory);
		expect(result.changed).toBe(true);
	});
});

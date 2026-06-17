import { describe, expect, it } from 'vitest';

import {
	mergeHistorySnapshot,
	mergeServerMessage,
	shouldApplySocketContentEvent
} from './historySync';

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

	it('keeps distinct evidence refs from the same source file when merging replay snapshots', () => {
		const existingSources = [
			{
				source: { id: 'doc-1', name: 'Doc 1' },
				metadata: [{ source: 'doc-1', evidence_ref: 'ke:doc-1:1' }]
			}
		];
		const incomingSources = [
			{
				source: { id: 'doc-1', name: 'Doc 1' },
				metadata: [{ source: 'doc-1', evidence_ref: 'ke:doc-1:2' }]
			}
		];
		const merged = mergeServerMessage(
			{ id: 'a', sources: existingSources as any },
			{ id: 'a', sources: incomingSources as any }
		);

		expect(merged.sources).toHaveLength(2);
		expect(merged.sources).toEqual(
			expect.arrayContaining([
				expect.objectContaining({
					metadata: [expect.objectContaining({ evidence_ref: 'ke:doc-1:1' })]
				}),
				expect.objectContaining({
					metadata: [expect.objectContaining({ evidence_ref: 'ke:doc-1:2' })]
				})
			])
		);
	});

	it('preserves and merges message metadata citation maps across replay snapshots', () => {
		const merged = mergeServerMessage(
			{
				id: 'a',
				metadata: {
					citation_map: { '1': 'ke:doc-1:1' },
					other: 'keep'
				}
			},
			{
				id: 'a',
				metadata: {
					citation_map: { '2': 'ke:doc-1:2' }
				}
			}
		);

		expect(merged.metadata).toEqual({
			citation_map: {
				'1': 'ke:doc-1:1',
				'2': 'ke:doc-1:2'
			},
			other: 'keep'
		});
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

describe('shouldApplySocketContentEvent', () => {
	it('ignores socket incremental assistant content for Agent Mode messages', () => {
		const agentMessage = {
			id: 'assistant-msg',
			role: 'assistant',
			content: 'SSE final answer',
			agent_run_id: 'run-1',
			done: false
		};

		expect(shouldApplySocketContentEvent(agentMessage, 'chat:completion')).toBe(false);
		expect(shouldApplySocketContentEvent(agentMessage, 'chat:message:delta')).toBe(false);
		expect(shouldApplySocketContentEvent(agentMessage, 'message')).toBe(false);
		expect(shouldApplySocketContentEvent(agentMessage, 'status')).toBe(true);
	});

	it('keeps legacy socket content events enabled when Agent Mode is absent', () => {
		const legacyMessage = {
			id: 'assistant-msg',
			role: 'assistant',
			content: '',
			done: false
		};

		expect(shouldApplySocketContentEvent(legacyMessage, 'chat:completion')).toBe(true);
		expect(shouldApplySocketContentEvent(legacyMessage, 'chat:message:delta')).toBe(true);
		expect(shouldApplySocketContentEvent(legacyMessage, 'message')).toBe(true);
	});

	it('allows final replace-style socket updates for Agent Mode messages without appending text', () => {
		const agentMessage = {
			id: 'assistant-msg',
			role: 'assistant',
			content: 'SSE final answer',
			agent_run_id: 'run-1',
			done: true
		};

		expect(shouldApplySocketContentEvent(agentMessage, 'chat:message')).toBe(true);
		expect(shouldApplySocketContentEvent(agentMessage, 'replace')).toBe(true);
	});
});

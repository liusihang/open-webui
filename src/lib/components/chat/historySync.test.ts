import { describe, expect, it } from 'vitest';

import {
	mergeHistorySnapshot,
	mergeServerMessage,
	prepareLoadedChatHistory,
	shouldApplySocketContentEvent
} from './historySync';
import { foldAgentRunEvents } from './AgentEvents/eventFold';
import { agentRunEventFixture } from './AgentEvents/fixtures';
import { createAgentRunRenderModel } from './AgentEvents/renderModel';

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

	it('marks persisted Agent Mode run ids as renderable reload changes without duplicating final text', () => {
		const currentHistory = {
			currentId: 'a',
			messages: {
				a: {
					id: 'a',
					role: 'assistant',
					content: 'Final answer from persisted message.',
					done: true
				}
			}
		};

		const latestHistory = {
			currentId: 'a',
			messages: {
				a: {
					id: 'a',
					role: 'assistant',
					content: 'Final answer from persisted message.',
					done: true,
					agent_run_id: 'run-1'
				}
			}
		};

		const result = mergeHistorySnapshot(currentHistory, latestHistory);

		expect(result.history.messages.a.agent_run_id).toBe('run-1');
		expect(result.history.messages.a.content).toBe('Final answer from persisted message.');
		expect(result.changed).toBe(true);
		expect(result.hasRenderableAssistantUpdate).toBe(true);
	});

	it('recovers an empty Agent Mode assistant shell through run event backfill after reload', () => {
		const currentHistory = {
			currentId: 'a',
			messages: {
				a: {
					id: 'a',
					role: 'assistant',
					content: '',
					done: false
				}
			}
		};

		const latestHistory = {
			currentId: 'a',
			messages: {
				a: {
					id: 'a',
					role: 'assistant',
					content: '',
					done: false,
					agent_run_id: 'run-1'
				}
			}
		};

		const result = mergeHistorySnapshot(currentHistory, latestHistory);
		const recoveredMessage = result.history.messages.a;
		const eventModel = createAgentRunRenderModel(
			foldAgentRunEvents([
				agentRunEventFixture({
					seq: 1,
					event_type: 'run.running',
					summary: 'Agent started'
				}),
				agentRunEventFixture({
					seq: 2,
					event_type: 'final.started',
					summary: 'Writing final answer'
				}),
				agentRunEventFixture({
					seq: 3,
					event_type: 'final.delta',
					payload: { delta: 'Recovered final answer.', delta_index: 0, final_stream_id: 'final-1' }
				}),
				agentRunEventFixture({
					seq: 3,
					event_type: 'final.delta',
					payload: { delta: 'Recovered final answer.', delta_index: 0, final_stream_id: 'final-1' }
				}),
				agentRunEventFixture({
					seq: 4,
					event_type: 'run.completed'
				})
			]),
			{ transportStatus: 'live' }
		);

		expect(recoveredMessage.agent_run_id).toBe('run-1');
		expect(result.changed).toBe(true);
		expect(result.hasRenderableAssistantUpdate).toBe(true);
		expect(Boolean(recoveredMessage.agent_run_id)).toBe(true);
		expect((recoveredMessage.content ?? '').trim()).toBe('');
		expect(eventModel.groups).toEqual([]);
		expect(eventModel.debugGroups.map((group) => group.kind)).toEqual(['run', 'run']);
		expect(eventModel.finalAnswer?.content).toBe('Recovered final answer.');
	});
});

describe('prepareLoadedChatHistory', () => {
	it('keeps an in-flight Agent Mode assistant message recoverable when legacy task ids are absent', () => {
		const agentRunId = '817af0ee-e301-41e1-aeab-aedd9e1fb354';
		const latestHistory = {
			currentId: 'assistant-msg',
			messages: {
				'assistant-msg': {
					id: 'assistant-msg',
					role: 'assistant',
					content: '',
					done: false,
					agent_run_id: agentRunId
				}
			}
		};

		const result = prepareLoadedChatHistory({ currentId: null, messages: {} }, latestHistory, []);

		expect(result.history.messages['assistant-msg'].agent_run_id).toBe(agentRunId);
		expect(result.history.messages['assistant-msg'].done).toBe(false);
		expect(result.taskIds).toBeNull();
		expect(result.hasRenderableAssistantUpdate).toBe(true);
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

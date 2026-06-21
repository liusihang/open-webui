import { describe, expect, it } from 'vitest';

import { foldAgentEventIntoStatusHistory, type AgentStatusEntry } from './agentStatusAdapter';
import { agentRunEventFixture } from './fixtures';
import type { AgentRunEvent } from './types';

const thinkingEntry = (overrides: Partial<AgentStatusEntry> = {}): AgentStatusEntry => ({
	id: 'thinking:run',
	done: false,
	action: 'agent_thinking',
	description: '思考中...',
	kind: 'thinking',
	seq: 1,
	created_at: 1_718_000_000,
	...overrides
});

describe('foldAgentEventIntoStatusHistory - thinking status', () => {
	it('creates a thinking entry on run.queued', () => {
		const event = agentRunEventFixture({ seq: 1, event_type: 'run.queued', summary: 'Queued' });

		const next = foldAgentEventIntoStatusHistory([], event);

		expect(next).toHaveLength(1);
		expect(next[0]).toMatchObject({
			id: 'thinking:run',
			kind: 'thinking',
			done: false,
			action: 'agent_thinking',
			seq: 1
		});
	});

	it('keeps thinking running on run.running', () => {
		const event = agentRunEventFixture({ seq: 2, event_type: 'run.running', summary: 'Running' });

		const next = foldAgentEventIntoStatusHistory([thinkingEntry({ seq: 1 })], event);

		expect(next).toHaveLength(1);
		expect(next[0]).toMatchObject({ id: 'thinking:run', kind: 'thinking', done: false, seq: 2 });
	});

	it('marks thinking done on run.completed', () => {
		const event = agentRunEventFixture({ seq: 5, event_type: 'run.completed', summary: 'Done' });

		const next = foldAgentEventIntoStatusHistory([thinkingEntry({ seq: 1 })], event);

		expect(next).toHaveLength(1);
		expect(next[0].done).toBe(true);
	});

	it('marks thinking done on run.cancelled', () => {
		const event = agentRunEventFixture({
			seq: 3,
			event_type: 'run.cancelled',
			summary: 'Cancelled'
		});

		const next = foldAgentEventIntoStatusHistory([thinkingEntry({ seq: 1 })], event);

		expect(next[0].done).toBe(true);
	});
});

describe('foldAgentEventIntoStatusHistory - tool status', () => {
	it('creates a tool entry on tool.requested with humanized name and input args', () => {
		const event = agentRunEventFixture({
			seq: 2,
			event_type: 'tool.requested',
			payload: {
				tool_call_id: 'tc-1',
				tool_name: 'web_search',
				arguments: { query: 'openwebui' }
			}
		});

		const next = foldAgentEventIntoStatusHistory([thinkingEntry({ seq: 1 })], event);

		expect(next).toHaveLength(2);
		expect(next[1]).toMatchObject({
			id: 'tool:tc-1',
			kind: 'tool',
			done: false,
			description: 'Web Search',
			detail: { input: { query: 'openwebui' } }
		});
	});

	it('updates the same tool entry on tool.started (upsert, not append)', () => {
		const requested = agentRunEventFixture({
			seq: 2,
			event_type: 'tool.requested',
			payload: { tool_call_id: 'tc-1', tool_name: 'web_search', arguments: { query: 'x' } }
		});
		const started = agentRunEventFixture({
			seq: 3,
			event_type: 'tool.started',
			payload: { tool_call_id: 'tc-1', tool_name: 'web_search' }
		});

		let history = foldAgentEventIntoStatusHistory([thinkingEntry({ seq: 1 })], requested);
		history = foldAgentEventIntoStatusHistory(history, started);

		const toolEntries = history.filter((entry) => entry.kind === 'tool');
		expect(toolEntries).toHaveLength(1);
		expect(toolEntries[0].seq).toBe(3);
	});

	it('marks tool done with output on tool.completed', () => {
		const requested = agentRunEventFixture({
			seq: 2,
			event_type: 'tool.requested',
			payload: { tool_call_id: 'tc-1', tool_name: 'web_search', arguments: { query: 'x' } }
		});
		const completed = agentRunEventFixture({
			seq: 4,
			event_type: 'tool.completed',
			payload: { tool_call_id: 'tc-1', tool_name: 'web_search', result: { hits: 3 } }
		});

		let history = foldAgentEventIntoStatusHistory([thinkingEntry({ seq: 1 })], requested);
		history = foldAgentEventIntoStatusHistory(history, completed);

		const tool = history.find((entry) => entry.id === 'tool:tc-1');
		expect(tool?.done).toBe(true);
		expect(tool?.detail?.output).toEqual({ hits: 3 });
	});

	it('converts tool entry to error on tool.failed', () => {
		const requested = agentRunEventFixture({
			seq: 2,
			event_type: 'tool.requested',
			payload: { tool_call_id: 'tc-1', tool_name: 'web_search', arguments: {} }
		});
		const failed = agentRunEventFixture({
			seq: 4,
			event_type: 'tool.failed',
			payload: { tool_call_id: 'tc-1', error: { message: 'boom', code: 'TIMEOUT' } }
		});

		let history = foldAgentEventIntoStatusHistory([thinkingEntry({ seq: 1 })], requested);
		history = foldAgentEventIntoStatusHistory(history, failed);

		const tool = history.find((entry) => entry.id === 'tool:tc-1');
		expect(tool?.kind).toBe('error');
		expect(tool?.done).toBe(true);
		expect(tool?.detail?.error).toEqual({ message: 'boom', code: 'TIMEOUT' });
	});

	it('falls back to seq-based id when tool_call_id missing', () => {
		const event = agentRunEventFixture({
			seq: 2,
			event_type: 'tool.requested',
			payload: { tool_name: 'calc' }
		});

		const next = foldAgentEventIntoStatusHistory([], event);

		expect(next[0].id).toBe('tool:seq-2');
	});
});

describe('foldAgentEventIntoStatusHistory - approval status', () => {
	it('creates an approval entry on approval.requested', () => {
		const event = agentRunEventFixture({
			seq: 3,
			event_type: 'approval.requested',
			payload: {
				approval_id: 'ap-1',
				action: 'run_command',
				description: '运行 npm test'
			}
		});

		const next = foldAgentEventIntoStatusHistory([thinkingEntry({ seq: 1 })], event);

		expect(next).toHaveLength(2);
		expect(next[1]).toMatchObject({
			id: 'approval:ap-1',
			kind: 'approval',
			done: false,
			description: '运行 npm test',
			detail: { input: { action: 'run_command' } }
		});
	});

	it('marks approval done on approval.completed', () => {
		const requested = agentRunEventFixture({
			seq: 3,
			event_type: 'approval.requested',
			payload: { approval_id: 'ap-1', action: 'run_command', description: '运行 npm test' }
		});
		const completed = agentRunEventFixture({
			seq: 5,
			event_type: 'approval.completed',
			payload: { approval_id: 'ap-1', decision: 'approved' }
		});

		let history = foldAgentEventIntoStatusHistory([thinkingEntry({ seq: 1 })], requested);
		history = foldAgentEventIntoStatusHistory(history, completed);

		const approval = history.find((entry) => entry.id === 'approval:ap-1');
		expect(approval?.done).toBe(true);
	});
});

describe('foldAgentEventIntoStatusHistory - artifact status', () => {
	it('creates a done artifact entry on artifact.registered', () => {
		const event = agentRunEventFixture({
			seq: 4,
			event_type: 'artifact.registered',
			payload: {
				artifact_id: 'ar-1',
				name: 'report.md',
				mime_type: 'text/markdown',
				path: '/tmp/report.md',
				size: '1024'
			}
		});

		const next = foldAgentEventIntoStatusHistory([thinkingEntry({ seq: 1 })], event);

		expect(next).toHaveLength(2);
		expect(next[1]).toMatchObject({
			id: 'artifact:ar-1',
			kind: 'artifact',
			done: true,
			description: 'report.md',
			detail: {
				artifact: {
					id: 'ar-1',
					name: 'report.md',
					mimeType: 'text/markdown',
					path: '/tmp/report.md',
					size: '1024'
				}
			}
		});
	});
});

describe('foldAgentEventIntoStatusHistory - subagent status', () => {
	it('creates a subagent entry on subagent.created', () => {
		const event = agentRunEventFixture({
			seq: 3,
			event_type: 'subagent.created',
			participant_id: 'p-1',
			payload: { name: 'researcher' }
		});

		const next = foldAgentEventIntoStatusHistory([thinkingEntry({ seq: 1 })], event);

		expect(next).toHaveLength(2);
		expect(next[1]).toMatchObject({
			id: 'subagent:p-1',
			kind: 'subagent',
			done: false,
			description: 'researcher'
		});
	});

	it('marks subagent done with result summary on subagent.completed', () => {
		const created = agentRunEventFixture({
			seq: 3,
			event_type: 'subagent.created',
			participant_id: 'p-1',
			payload: { name: 'researcher' }
		});
		const completed = agentRunEventFixture({
			seq: 6,
			event_type: 'subagent.completed',
			participant_id: 'p-1',
			payload: { result_summary: 'Found 3 papers' }
		});

		let history = foldAgentEventIntoStatusHistory([thinkingEntry({ seq: 1 })], created);
		history = foldAgentEventIntoStatusHistory(history, completed);

		const sub = history.find((entry) => entry.id === 'subagent:p-1');
		expect(sub?.done).toBe(true);
		expect(sub?.detail?.subagent?.resultSummary).toBe('Found 3 papers');
	});

	it('converts subagent to error on subagent.failed', () => {
		const created = agentRunEventFixture({
			seq: 3,
			event_type: 'subagent.created',
			participant_id: 'p-1',
			payload: { name: 'researcher' }
		});
		const failed = agentRunEventFixture({
			seq: 6,
			event_type: 'subagent.failed',
			participant_id: 'p-1',
			payload: { error: { message: 'crashed' } }
		});

		let history = foldAgentEventIntoStatusHistory([thinkingEntry({ seq: 1 })], created);
		history = foldAgentEventIntoStatusHistory(history, failed);

		const sub = history.find((entry) => entry.id === 'subagent:p-1');
		expect(sub?.kind).toBe('error');
		expect(sub?.done).toBe(true);
	});
});

describe('foldAgentEventIntoStatusHistory - step status', () => {
	it('creates a done step entry on action.summary', () => {
		const event = agentRunEventFixture({
			seq: 4,
			event_type: 'action.summary',
			summary: '读取文件 foo.py'
		});

		const next = foldAgentEventIntoStatusHistory([thinkingEntry({ seq: 1 })], event);

		expect(next).toHaveLength(2);
		expect(next[1]).toMatchObject({
			id: 'step:4',
			kind: 'step',
			done: true,
			description: '读取文件 foo.py'
		});
	});
});

describe('foldAgentEventIntoStatusHistory - run error status', () => {
	it('appends an error entry on run.failed and marks thinking done', () => {
		const event = agentRunEventFixture({
			seq: 9,
			event_type: 'run.failed',
			payload: { error: { message: 'agent crashed' } }
		});

		const next = foldAgentEventIntoStatusHistory([thinkingEntry({ seq: 1 })], event);

		expect(next).toHaveLength(2);
		expect(next[0].id).toBe('thinking:run');
		expect(next[0].done).toBe(true);
		expect(next[1]).toMatchObject({
			kind: 'error',
			done: true,
			detail: { error: { message: 'agent crashed' } }
		});
	});

	it('appends an error entry on run.budget_exceeded', () => {
		const event = agentRunEventFixture({
			seq: 9,
			event_type: 'run.budget_exceeded',
			summary: '预算超限'
		});

		const next = foldAgentEventIntoStatusHistory([thinkingEntry({ seq: 1 })], event);

		expect(next.some((entry) => entry.kind === 'error')).toBe(true);
	});
});

describe('foldAgentEventIntoStatusHistory - text status', () => {
	it('accumulates text.delta content per block id', () => {
		const first = agentRunEventFixture({
			seq: 2,
			event_type: 'text.delta',
			payload: { block_id: 'block-1', delta: 'Hello ' }
		});
		const second = agentRunEventFixture({
			seq: 3,
			event_type: 'text.delta',
			payload: { block_id: 'block-1', delta: 'world' }
		});

		let history = foldAgentEventIntoStatusHistory([thinkingEntry({ seq: 1 })], first);
		history = foldAgentEventIntoStatusHistory(history, second);

		expect(history).toHaveLength(2);
		expect(history[1]).toMatchObject({
			id: 'text:block-1',
			kind: 'text',
			done: false,
			detail: { text: { blockId: 'block-1', content: 'Hello world', participantId: 'leader' } }
		});
	});

	it('marks open text segments done when the run pivots to a tool', () => {
		const text = agentRunEventFixture({
			seq: 2,
			event_type: 'text.delta',
			payload: { block_id: 'block-1', delta: 'Let me check that.' }
		});
		const tool = agentRunEventFixture({
			seq: 3,
			event_type: 'tool.requested',
			payload: { tool_call_id: 'tc-1', tool_name: 'web_search', arguments: { query: 'x' } }
		});

		let history = foldAgentEventIntoStatusHistory([thinkingEntry({ seq: 1 })], text);
		history = foldAgentEventIntoStatusHistory(history, tool);

		expect(history).toHaveLength(3);
		expect(history[1]).toMatchObject({
			id: 'text:block-1',
			kind: 'text',
			done: true,
			seq: 3
		});
		expect(history[2]).toMatchObject({
			id: 'tool:tc-1',
			kind: 'tool',
			done: false
		});
	});

	it('ignores text.delta without a block id', () => {
		const event = agentRunEventFixture({
			seq: 2,
			event_type: 'text.delta',
			payload: { delta: 'Hello world' }
		});

		const next = foldAgentEventIntoStatusHistory([thinkingEntry({ seq: 1 })], event);

		expect(next).toHaveLength(1);
		expect(next[0].id).toBe('thinking:run');
	});
});

describe('foldAgentEventIntoStatusHistory - ignored events', () => {
	it('does not create entries for model.selection.* events', () => {
		const event = agentRunEventFixture({
			seq: 2,
			event_type: 'model.selection.requested',
			payload: { model_id: 'gpt-4' }
		});

		const next = foldAgentEventIntoStatusHistory([thinkingEntry({ seq: 1 })], event);

		expect(next).toHaveLength(1);
		expect(next[0].id).toBe('thinking:run');
	});

	it('does not create entries for final.* events', () => {
		const started = agentRunEventFixture({ seq: 2, event_type: 'final.started' });
		const delta = agentRunEventFixture({
			seq: 3,
			event_type: 'final.delta',
			payload: { stream_id: 's1', delta_index: 0, text: 'Hello' }
		});

		let history = foldAgentEventIntoStatusHistory([thinkingEntry({ seq: 1 })], started);
		history = foldAgentEventIntoStatusHistory(history, delta);

		expect(history).toHaveLength(1);
		expect(history[0].id).toBe('thinking:run');
	});
});

describe('foldAgentEventIntoStatusHistory - ordinary Q&A full flow', () => {
	it('keeps the streamed answer as a text entry for a no-tool run', () => {
		const events: AgentRunEvent[] = [
			agentRunEventFixture({ seq: 1, event_type: 'run.queued', summary: 'Queued' }),
			agentRunEventFixture({ seq: 2, event_type: 'run.running', summary: 'Running' }),
			agentRunEventFixture({
				seq: 3,
				event_type: 'text.delta',
				payload: { block_id: 'answer', delta: 'Hello' }
			}),
			agentRunEventFixture({ seq: 4, event_type: 'run.completed', summary: 'Done' })
		];

		const history = events.reduce(
			(acc, event) => foldAgentEventIntoStatusHistory(acc, event),
			[] as AgentStatusEntry[]
		);

		expect(history).toHaveLength(2);
		expect(history[0]).toMatchObject({ id: 'thinking:run', kind: 'thinking', done: true });
		expect(history[1]).toMatchObject({
			id: 'text:answer',
			kind: 'text',
			done: true,
			detail: { text: { blockId: 'answer', content: 'Hello' } }
		});
	});
});

describe('foldAgentEventIntoStatusHistory - immutability', () => {
	it('returns a new array reference without mutating the input', () => {
		const original = [thinkingEntry({ seq: 1 })];
		const event = agentRunEventFixture({
			seq: 2,
			event_type: 'tool.requested',
			payload: { tool_call_id: 'tc-1', tool_name: 'web_search', arguments: {} }
		});

		const next = foldAgentEventIntoStatusHistory(original, event);

		expect(next).not.toBe(original);
		expect(original).toHaveLength(1);
		expect(next).toHaveLength(2);
	});

	it('preserves existing chatbot status entries (web_search) untouched', () => {
		const webSearchEntry: AgentStatusEntry = {
			id: 'web_search:1',
			done: true,
			action: 'web_search',
			description: 'Searched 3 sites',
			kind: 'step',
			seq: 0,
			created_at: 1_718_000_000,
			urls: ['https://example.com']
		};
		const event = agentRunEventFixture({ seq: 1, event_type: 'run.queued' });

		const next = foldAgentEventIntoStatusHistory([webSearchEntry], event);

		expect(next).toContainEqual(webSearchEntry);
		expect(next).toHaveLength(2);
	});
});

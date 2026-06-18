import { describe, expect, it } from 'vitest';

import { createAgentRunEventState, foldAgentRunEvent, foldAgentRunEvents } from './eventFold';
import { agentRunEventFixture } from './fixtures';

describe('foldAgentRunEvents', () => {
	it('orders run events by seq and ignores duplicate or older events', () => {
		const state = foldAgentRunEvents([
			agentRunEventFixture({ seq: 3, event_type: 'tool.completed', summary: 'Search completed' }),
			agentRunEventFixture({ seq: 1, event_type: 'run.running', summary: 'Agent started' }),
			agentRunEventFixture({ seq: 2, event_type: 'tool.started', summary: 'Searching docs' }),
			agentRunEventFixture({
				seq: 2,
				event_type: 'tool.started',
				summary: 'Duplicate should not render'
			}),
			agentRunEventFixture({
				seq: 1,
				event_type: 'run.running',
				summary: 'Old replay should not render'
			})
		]);

		expect(state.lastSeq).toBe(3);
		expect(state.items.map((item) => item.summary)).toEqual([
			'Agent started',
			'Searching docs',
			'Search completed'
		]);
	});

	it('accepts reconnect backfill for unseen lower sequence events', () => {
		let state = createAgentRunEventState();

		state = foldAgentRunEvent(
			state,
			agentRunEventFixture({ seq: 1, event_type: 'run.running', summary: 'Agent started' })
		);
		state = foldAgentRunEvent(
			state,
			agentRunEventFixture({ seq: 4, event_type: 'tool.completed', summary: 'Search completed' })
		);
		state = foldAgentRunEvent(
			state,
			agentRunEventFixture({ seq: 2, event_type: 'action.summary', summary: 'Planning search' })
		);
		state = foldAgentRunEvent(
			state,
			agentRunEventFixture({ seq: 3, event_type: 'tool.started', summary: 'Searching docs' })
		);

		expect(state.lastSeq).toBe(4);
		expect(state.items.map((item) => item.summary)).toEqual([
			'Agent started',
			'Planning search',
			'Searching docs',
			'Search completed'
		]);
	});

	it('does not render final answer deltas before the final phase starts', () => {
		let state = createAgentRunEventState();

		state = foldAgentRunEvent(
			state,
			agentRunEventFixture({
				seq: 1,
				event_type: 'final.delta',
				payload: { delta: 'too early', delta_index: 0, final_stream_id: 'final-1' },
				phase: 'running'
			})
		);

		expect(state.finalText).toBe('');
		expect(state.items).toEqual([]);

		state = foldAgentRunEvent(
			state,
			agentRunEventFixture({ seq: 2, event_type: 'final.started', summary: 'Writing final answer' })
		);
		state = foldAgentRunEvent(
			state,
			agentRunEventFixture({
				seq: 3,
				event_type: 'final.delta',
				payload: { delta: 'final answer', delta_index: 0, final_stream_id: 'final-1' },
				phase: 'finalizing'
			})
		);

		expect(state.finalText).toBe('final answer');
		expect(state.items.map((item) => item.eventType)).toEqual(['final.started', 'final.delta']);
	});

	it('accumulates final deltas once across reconnect replays', () => {
		let state = createAgentRunEventState();

		state = foldAgentRunEvent(
			state,
			agentRunEventFixture({ seq: 1, event_type: 'final.started', summary: 'Writing final answer' })
		);
		state = foldAgentRunEvent(
			state,
			agentRunEventFixture({
				seq: 2,
				event_type: 'final.delta',
				summary: 'Hello ',
				payload: { delta: 'Hello ', delta_index: 0, final_stream_id: 'final-1' }
			})
		);
		state = foldAgentRunEvent(
			state,
			agentRunEventFixture({
				seq: 2,
				event_type: 'final.delta',
				summary: 'Hello ',
				payload: { delta: 'Hello ', delta_index: 0, final_stream_id: 'final-1' }
			})
		);
		state = foldAgentRunEvent(
			state,
			agentRunEventFixture({
				seq: 3,
				event_type: 'final.delta',
				summary: 'world',
				payload: { delta: 'world', delta_index: 1, final_stream_id: 'final-1' }
			})
		);

		expect(state.lastSeq).toBe(3);
		expect(state.finalText).toBe('Hello world');
		expect(state.items.map((item) => item.eventType)).toEqual(['final.started', 'final.delta']);
	});

	it('surfaces concise details and strips raw reasoning fields', () => {
		const state = foldAgentRunEvents([
			agentRunEventFixture({
				seq: 1,
				event_type: 'action.summary',
				summary: 'Deciding next step',
				payload: {
					status: 'running',
					reasoning: 'hidden chain of thought',
					raw_reasoning: 'hidden raw chain',
					thought: 'hidden thought',
					private: 'hidden private text',
					details: {
						next: 'search docs',
						chain_of_thought: 'hidden nested chain'
					}
				}
			}),
			agentRunEventFixture({
				seq: 2,
				event_type: 'tool.completed',
				summary: '',
				payload: {
					tool_name: 'search',
					status: 'success',
					content: 'Found three docs',
					raw: { massive: 'backend-only payload' },
					structured_error: null
				}
			})
		]);

		expect(state.items[0]).toMatchObject({
			eventType: 'action.summary',
			summary: 'Deciding next step',
			details: { status: 'running', details: { next: 'search docs' } }
		});
		expect(state.items[1]).toMatchObject({
			eventType: 'tool.completed',
			summary: 'Completed search',
			details: {
				tool_name: 'search',
				status: 'success',
				content: 'Found three docs',
				structured_error: null
			}
		});
		expect(JSON.stringify(state)).not.toContain('hidden');
		expect(JSON.stringify(state)).not.toContain('massive');
	});

	it('keeps expandable approval artifact tool warning and subagent details', () => {
		const state = foldAgentRunEvents([
			agentRunEventFixture({
				seq: 1,
				event_type: 'approval.requested',
				summary: '',
				payload: { approval_id: 'approval-1', action: 'overwrite report.txt' }
			}),
			agentRunEventFixture({
				seq: 2,
				event_type: 'artifact.registered',
				summary: '',
				payload: {
					artifact_id: 'artifact-1',
					path: '/workspace/agent-runs/run-1/outputs/report.txt',
					mime_type: 'text/plain'
				}
			}),
			agentRunEventFixture({
				seq: 3,
				event_type: 'tool.completed',
				summary: '',
				payload: {
					tool_name: 'run_command',
					status: 'success',
					warnings: [{ code: 'still_running', message: 'Process remains active' }],
					process_refs: [{ process_id: 'proc-1', status: 'running' }]
				}
			}),
			agentRunEventFixture({
				seq: 4,
				event_type: 'tool.failed',
				summary: '',
				payload: {
					tool_name: 'delete_file',
					status: 'error',
					structured_error: { code: 'permission_denied', message: 'Denied' }
				}
			}),
			agentRunEventFixture({
				seq: 5,
				event_type: 'subagent.completed',
				participant_id: 'subagent-1',
				summary: '',
				payload: { status: 'completed', result_summary: 'Checked citations' }
			})
		]);

		expect(state.items.map((item) => item.summary)).toEqual([
			'Approval requested',
			'Registered /workspace/agent-runs/run-1/outputs/report.txt',
			'Completed run_command',
			'delete_file failed',
			'Completed subagent-1'
		]);
		expect(state.items.map((item) => item.status)).toEqual([
			'running',
			'done',
			'done',
			'error',
			'done'
		]);
		expect(state.items[2].details).toMatchObject({
			warnings: [{ code: 'still_running', message: 'Process remains active' }],
			process_refs: [{ process_id: 'proc-1', status: 'running' }]
		});
		expect(state.items[3].details).toMatchObject({
			structured_error: { code: 'permission_denied', message: 'Denied' }
		});
		expect(state.items[4].participantId).toBe('subagent-1');
	});
});

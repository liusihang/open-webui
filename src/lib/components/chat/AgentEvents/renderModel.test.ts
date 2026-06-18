import { describe, expect, it } from 'vitest';

import { foldAgentRunEvents } from './eventFold';
import { agentRunEventFixture } from './fixtures';
import { createAgentRunRenderModel } from './renderModel';
import type { AgentRunEvent } from './types';

describe('createAgentRunRenderModel', () => {
	it('groups a tool lifecycle into one tool panel with input and output sections', () => {
		const state = foldAgentRunEvents([
			agentRunEventFixture({
				seq: 1,
				event_type: 'tool.requested',
				summary: '',
				payload: {
					tool_call_id: 'tool-call-1',
					tool_name: 'query_knowledge',
					arguments: { query: 'agent mode UI' }
				}
			}),
			agentRunEventFixture({
				seq: 2,
				event_type: 'tool.started',
				summary: '',
				payload: {
					tool_call_id: 'tool-call-1',
					tool_name: 'query_knowledge',
					status: 'running'
				}
			}),
			agentRunEventFixture({
				seq: 3,
				event_type: 'tool.completed',
				summary: '',
				payload: {
					tool_call_id: 'tool-call-1',
					tool_name: 'query_knowledge',
					result: { documents: 3 },
					warnings: [{ message: 'One document was skipped' }]
				}
			})
		]);

		const model = createAgentRunRenderModel(state, { transportStatus: 'live' });

		expect(model.groups).toHaveLength(1);
		expect(model.groups[0]).toMatchObject({
			kind: 'tool',
			status: 'done',
			title: 'query_knowledge',
			seqRange: { start: 1, end: 3 }
		});
		expect(model.groups[0].detailSections.map((section) => section.kind)).toEqual([
			'input',
			'output'
		]);
		expect(JSON.stringify(model.groups[0].detailSections)).toContain('agent mode UI');
		expect(JSON.stringify(model.groups[0].detailSections)).toContain('One document was skipped');
	});

	it('marks failed tools as errors and exposes an error section', () => {
		const state = foldAgentRunEvents([
			agentRunEventFixture({
				seq: 1,
				event_type: 'tool.started',
				payload: { tool_call_id: 'tool-call-1', tool_name: 'delete_file' }
			}),
			agentRunEventFixture({
				seq: 2,
				event_type: 'tool.failed',
				payload: {
					tool_call_id: 'tool-call-1',
					tool_name: 'delete_file',
					structured_error: { code: 'permission_denied', message: 'Denied' }
				}
			})
		]);

		const model = createAgentRunRenderModel(state, { transportStatus: 'live' });

		expect(model.groups[0]).toMatchObject({
			kind: 'tool',
			status: 'error',
			title: 'delete_file'
		});
		expect(model.groups[0].detailSections).toEqual(
			expect.arrayContaining([
				expect.objectContaining({
					kind: 'error',
					title: 'Error'
				})
			])
		);
		expect(model.errors).toHaveLength(1);
	});

	it('groups approval requested and completed into one approval part', () => {
		const state = foldAgentRunEvents([
			agentRunEventFixture({
				seq: 1,
				event_type: 'approval.requested',
				payload: { approval_id: 'approval-1', action: 'overwrite report.txt' }
			}),
			agentRunEventFixture({
				seq: 2,
				event_type: 'approval.completed',
				payload: { approval_id: 'approval-1', status: 'approved', decision: 'approved' }
			})
		]);

		const model = createAgentRunRenderModel(state, { transportStatus: 'live' });

		expect(model.groups).toHaveLength(1);
		expect(model.groups[0]).toMatchObject({
			kind: 'approval',
			status: 'done',
			title: 'Approval completed',
			subtitle: 'overwrite report.txt',
			seqRange: { start: 1, end: 2 }
		});
	});

	it('aggregates artifact events into timeline groups and artifact parts', () => {
		const state = foldAgentRunEvents([
			agentRunEventFixture({
				seq: 1,
				event_type: 'artifact.registered',
				payload: {
					artifact_id: 'artifact-1',
					name: 'report.md',
					path: '/workspace/report.md',
					mime_type: 'text/markdown',
					size: 1234
				}
			})
		]);

		const model = createAgentRunRenderModel(state, { transportStatus: 'live' });

		expect(model.groups[0]).toMatchObject({
			kind: 'artifact',
			status: 'done',
			title: 'report.md'
		});
		expect(model.artifacts).toEqual([
			expect.objectContaining({
				id: 'artifact-1',
				name: 'report.md',
				path: '/workspace/report.md',
				mimeType: 'text/markdown',
				size: '1234'
			})
		]);
	});

	it('groups subagent lifecycle by participant id', () => {
		const state = foldAgentRunEvents([
			agentRunEventFixture({
				seq: 1,
				event_type: 'subagent.created',
				participant_id: 'subagent-1',
				payload: { participant_name: 'Citation checker', model_id: 'gpt-5.4' }
			}),
			agentRunEventFixture({
				seq: 2,
				event_type: 'subagent.completed',
				participant_id: 'subagent-1',
				payload: { result_summary: 'Checked citations' }
			})
		]);

		const model = createAgentRunRenderModel(state, { transportStatus: 'live' });

		expect(model.groups).toHaveLength(1);
		expect(model.groups[0]).toMatchObject({
			kind: 'subagent',
			status: 'done',
			title: 'Citation checker',
			subtitle: 'Checked citations',
			metadata: expect.arrayContaining([{ label: 'Model', value: 'gpt-5.4' }])
		});
	});

	it('keeps final answer out of ordinary groups and exposes it as finalAnswer', () => {
		const state = foldAgentRunEvents([
			agentRunEventFixture({ seq: 1, event_type: 'run.running' }),
			agentRunEventFixture({
				seq: 2,
				event_type: 'final.started',
				summary: 'Writing final answer'
			}),
			agentRunEventFixture({
				seq: 3,
				event_type: 'final.delta',
				payload: { delta: 'Done.', delta_index: 0, final_stream_id: 'final-1' }
			}),
			agentRunEventFixture({ seq: 4, event_type: 'run.completed' })
		]);

		const model = createAgentRunRenderModel(state, { transportStatus: 'live' });

		expect(model.groups.map((group) => group.kind)).toEqual(['run', 'run']);
		expect(model.finalAnswer).toMatchObject({
			status: 'done',
			content: 'Done.'
		});
	});

	it('falls back for unknown events without exposing private reasoning fields', () => {
		const unknownEvent = agentRunEventFixture({
			seq: 1,
			event_type: 'agent.internal.debug' as AgentRunEvent['event_type'],
			summary: 'Internal update',
			payload: {
				public: 'visible',
				reasoning: 'hidden',
				nested: { chain_of_thought: 'hidden nested', value: 'safe' }
			}
		});
		const state = foldAgentRunEvents([unknownEvent]);

		const model = createAgentRunRenderModel(state, { transportStatus: 'live' });

		expect(model.groups[0]).toMatchObject({
			kind: 'fallback',
			title: 'Internal update'
		});
		expect(JSON.stringify(model)).toContain('visible');
		expect(JSON.stringify(model)).toContain('safe');
		expect(JSON.stringify(model)).not.toContain('hidden');
	});

	it('does not duplicate groups on reconnect replays and preserves terminal run status', () => {
		const state = foldAgentRunEvents([
			agentRunEventFixture({ seq: 3, event_type: 'run.completed' }),
			agentRunEventFixture({
				seq: 1,
				event_type: 'tool.started',
				payload: { tool_call_id: 'tool-call-1', tool_name: 'search' }
			}),
			agentRunEventFixture({
				seq: 2,
				event_type: 'tool.completed',
				payload: { tool_call_id: 'tool-call-1', tool_name: 'search' }
			}),
			agentRunEventFixture({
				seq: 2,
				event_type: 'tool.completed',
				payload: { tool_call_id: 'tool-call-1', tool_name: 'search' }
			})
		]);

		const model = createAgentRunRenderModel(state, { transportStatus: 'reconnecting' });

		expect(model.runStatus).toBe('completed');
		expect(model.transportStatus).toBe('reconnecting');
		expect(model.groups.map((group) => group.kind)).toEqual(['tool', 'run']);
		expect(model.groups[0].seqRange).toEqual({ start: 1, end: 2 });
	});
});

import { describe, expect, it } from 'vitest';

import { createAgentRunEventState, foldAgentRunEvents } from './eventFold';
import { foldAgentRunEvent } from './eventFold';
import { buildAgentTranscriptModel } from './transcriptModel';
import { agentRunEventFixture } from './fixtures';

describe('buildAgentTranscriptModel', () => {
	it('groups tool lifecycle by tool_call_id and exposes a single tool part with running->done->error status', () => {
		const state = foldAgentRunEvents([
			agentRunEventFixture({
				seq: 1,
				event_type: 'tool.requested',
				summary: '',
				payload: { tool_call_id: 'call-1', tool_name: 'web_search' }
			}),
			agentRunEventFixture({
				seq: 2,
				event_type: 'tool.started',
				summary: '',
				payload: { tool_call_id: 'call-1', tool_name: 'web_search', status: 'running' }
			}),
			agentRunEventFixture({
				seq: 3,
				event_type: 'tool.completed',
				summary: '',
				payload: { tool_call_id: 'call-1', tool_name: 'web_search', status: 'success' }
			})
		]);

		const model = buildAgentTranscriptModel(state);
		const toolParts = model.parts.filter((part) => part.kind === 'tool');
		expect(toolParts).toHaveLength(1);
		const tool = toolParts[0];
		if (tool.kind !== 'tool') {
			throw new Error('expected tool part');
		}
		expect(tool.toolCallId).toBe('call-1');
		expect(tool.toolName).toBe('web_search');
		expect(tool.status).toBe('done');
		expect(model.summary.toolCount).toBe(1);
		expect(model.summary.hasError).toBe(false);
	});

	it('flags failed tools as error and expands them by default', () => {
		const state = foldAgentRunEvents([
			agentRunEventFixture({
				seq: 1,
				event_type: 'tool.requested',
				summary: '',
				payload: { tool_call_id: 'call-1', tool_name: 'delete_file' }
			}),
			agentRunEventFixture({
				seq: 2,
				event_type: 'tool.failed',
				summary: '',
				payload: {
					tool_call_id: 'call-1',
					tool_name: 'delete_file',
					structured_error: { code: 'permission_denied', message: 'Denied' }
				}
			})
		]);

		const model = buildAgentTranscriptModel(state);
		const tool = model.parts.find((part) => part.kind === 'tool');
		if (!tool || tool.kind !== 'tool') {
			throw new Error('expected tool part');
		}
		expect(tool.status).toBe('error');
		expect(tool.defaultExpanded).toBe(true);
		expect(model.summary.hasError).toBe(true);
	});

	it('exposes pending approvals and marks them defaultExpanded; closes them once completed', () => {
		const pending = buildAgentTranscriptModel(
			foldAgentRunEvents([
				agentRunEventFixture({
					seq: 1,
					event_type: 'approval.requested',
					summary: '',
					payload: { approval_id: 'appr-1', action: 'overwrite report.txt' }
				})
			])
		);
		const pendingApproval = pending.parts.find((part) => part.kind === 'approval');
		if (!pendingApproval || pendingApproval.kind !== 'approval') {
			throw new Error('expected approval part');
		}
		expect(pendingApproval.status).toBe('pending');
		expect(pendingApproval.defaultExpanded).toBe(true);
		expect(pending.summary.hasPendingApproval).toBe(true);

		const resolved = buildAgentTranscriptModel(
			foldAgentRunEvents([
				agentRunEventFixture({
					seq: 1,
					event_type: 'approval.requested',
					summary: '',
					payload: { approval_id: 'appr-1', action: 'overwrite report.txt' }
				}),
				agentRunEventFixture({
					seq: 2,
					event_type: 'approval.completed',
					summary: '',
					payload: { approval_id: 'appr-1', status: 'approved' }
				})
			])
		);
		const resolvedApproval = resolved.parts.find((part) => part.kind === 'approval');
		if (!resolvedApproval || resolvedApproval.kind !== 'approval') {
			throw new Error('expected approval part');
		}
		expect(resolvedApproval.status).toBe('approved');
		expect(resolvedApproval.defaultExpanded).toBe(false);
		expect(resolved.summary.hasPendingApproval).toBe(false);
	});

	it('renders artifact summary without exposing the raw path as the headline', () => {
		const longPath = '/workspace/agent-runs/run-1/outputs/deep/nested/report.txt';
		const state = foldAgentRunEvents([
			agentRunEventFixture({
				seq: 1,
				event_type: 'artifact.registered',
				summary: '',
				payload: {
					artifact_id: 'artifact-1',
					name: 'report.txt',
					path: longPath,
					mime_type: 'text/plain'
				}
			})
		]);

		const model = buildAgentTranscriptModel(state);
		const artifact = model.parts.find((part) => part.kind === 'artifact');
		if (!artifact || artifact.kind !== 'artifact') {
			throw new Error('expected artifact part');
		}
		expect(artifact.artifactId).toBe('artifact-1');
		expect(artifact.name).toBe('report.txt');
		expect(artifact.summary).not.toContain(longPath);
		expect(artifact.path).toBe(longPath);
		expect(model.summary.artifactCount).toBe(1);
	});

	it('extracts artifact details from runtime and service nested artifact payloads', () => {
		const state = foldAgentRunEvents([
			agentRunEventFixture({
				seq: 1,
				event_type: 'artifact.registered',
				summary: 'Artifact runtime-report.txt is ready.',
				payload: {
					tool_call_id: 'call-runtime',
					artifact: {
						id: 'artifact-runtime',
						name: 'runtime-report.txt',
						path: '/workspace/agent-runs/run-1/outputs/runtime-report.txt',
						mime_type: 'text/plain'
					}
				}
			}),
			agentRunEventFixture({
				seq: 2,
				event_type: 'artifact.registered',
				summary: 'Artifact registered: /workspace/agent-runs/run-1/outputs/service-report.md',
				payload: {
					tool_call_id: 'call-service',
					artifacts: [
						{
							id: 'artifact-service',
							name: 'service-report.md',
							path: '/workspace/agent-runs/run-1/outputs/service-report.md',
							mime_type: 'text/markdown'
						}
					]
				}
			})
		]);

		const model = buildAgentTranscriptModel(state);
		const artifacts = model.parts.filter((part) => part.kind === 'artifact');
		expect(artifacts).toHaveLength(2);
		expect(artifacts[0]).toMatchObject({
			artifactId: 'artifact-runtime',
			name: 'runtime-report.txt',
			path: '/workspace/agent-runs/run-1/outputs/runtime-report.txt',
			mimeType: 'text/plain'
		});
		expect(artifacts[1]).toMatchObject({
			artifactId: 'artifact-service',
			name: 'service-report.md',
			path: '/workspace/agent-runs/run-1/outputs/service-report.md',
			mimeType: 'text/markdown'
		});
	});

	it('uses approval.completed decision when resolving approval status', () => {
		const model = buildAgentTranscriptModel(
			foldAgentRunEvents([
				agentRunEventFixture({
					seq: 1,
					event_type: 'approval.requested',
					summary: '',
					payload: { approval_id: 'appr-1', action: 'delete report.txt' }
				}),
				agentRunEventFixture({
					seq: 2,
					event_type: 'approval.completed',
					summary: '',
					payload: { approval_id: 'appr-1', decision: 'rejected' }
				})
			])
		);

		const approval = model.parts.find((part) => part.kind === 'approval');
		if (!approval || approval.kind !== 'approval') {
			throw new Error('expected approval part');
		}
		expect(approval.status).toBe('rejected');
	});

	it('renders successful runtime subagent completion with worker name and result content', () => {
		const model = buildAgentTranscriptModel(
			foldAgentRunEvents([
				agentRunEventFixture({
					seq: 1,
					event_type: 'subagent.created',
					participant_id: 'subagent:run-1:1',
					summary: 'Subagent Researcher started.',
					payload: {
						participant_id: 'subagent:run-1:1',
						name: 'Researcher',
						task: 'Find the key fact'
					}
				}),
				agentRunEventFixture({
					seq: 2,
					event_type: 'subagent.completed',
					participant_id: 'subagent:run-1:1',
					summary: 'Subagent Researcher completed.',
					payload: {
						participant_id: 'subagent:run-1:1',
						name: 'Researcher',
						status: 'completed',
						content: 'Subagent found the key fact.'
					}
				})
			])
		);

		const subagent = model.parts.find((part) => part.kind === 'subagent');
		if (!subagent || subagent.kind !== 'subagent') {
			throw new Error('expected subagent part');
		}
		expect(subagent.status).toBe('done');
		expect(subagent.participantName).toBe('Researcher');
		expect(subagent.resultSummary).toBe('Subagent found the key fact.');
	});

	it('renders run.failed as a transcript error part that defaults to expanded', () => {
		const state = foldAgentRunEvents([
			agentRunEventFixture({ seq: 1, event_type: 'run.running' }),
			agentRunEventFixture({
				seq: 2,
				event_type: 'run.failed',
				summary: '',
				payload: { error: { message: 'agent crashed' } }
			})
		]);

		const model = buildAgentTranscriptModel(state);
		const errorPart = model.parts.find((part) => part.kind === 'error');
		expect(errorPart).toBeDefined();
		if (errorPart && errorPart.kind === 'error') {
			expect(errorPart.defaultExpanded).toBe(true);
		}
		expect(model.runStatus).toBe('failed');
		expect(model.summary.hasError).toBe(true);
	});

	it('falls back to legacy_note text part when text.delta arrives without block_kind', () => {
		const state = foldAgentRunEvents([
			agentRunEventFixture({
				seq: 1,
				event_type: 'text.delta',
				payload: { block_id: 'legacy-1', delta_index: 0, delta: 'legacy note' }
			})
		]);

		const model = buildAgentTranscriptModel(state);
		const textPart = model.parts.find((part) => part.kind === 'legacy_note');
		expect(textPart).toBeDefined();
		if (textPart && textPart.kind === 'legacy_note') {
			expect(textPart.text).toBe('legacy note');
			expect(textPart.textKind).toBe('legacy');
		}
		expect(model.final).toBeNull();
	});

	it('renders assistant_note and action_summary text blocks as separate transcript parts in seq order', () => {
		const state = foldAgentRunEvents([
			agentRunEventFixture({
				seq: 1,
				event_type: 'run.running',
				summary: 'Agent started'
			}),
			agentRunEventFixture({
				seq: 2,
				event_type: 'text.delta',
				payload: {
					block_id: 'note-1',
					delta_index: 0,
					delta: 'Planning the search',
					block_kind: 'assistant_note'
				}
			}),
			agentRunEventFixture({
				seq: 3,
				event_type: 'text.delta',
				payload: {
					block_id: 'summary-1',
					delta_index: 0,
					delta: 'Searching docs',
					block_kind: 'action_summary'
				}
			})
		]);

		const model = buildAgentTranscriptModel(state);
		const textParts = model.parts.filter((part) =>
			part.kind === 'assistant_note' || part.kind === 'action_summary'
		);
		expect(textParts.map((part) => part.kind)).toEqual(['assistant_note', 'action_summary']);
	});

	it('uses finalText as final answer content and never mixes text.delta blocks into it', () => {
		const state = foldAgentRunEvents([
			agentRunEventFixture({
				seq: 1,
				event_type: 'text.delta',
				payload: {
					block_id: 'note-1',
					delta_index: 0,
					delta: 'public thinking',
					block_kind: 'assistant_note'
				}
			}),
			agentRunEventFixture({ seq: 2, event_type: 'final.started', summary: 'Writing final answer' }),
			agentRunEventFixture({
				seq: 3,
				event_type: 'final.delta',
				payload: {
					delta: 'the real answer',
					delta_index: 0,
					final_stream_id: 'final-1'
				},
				phase: 'finalizing'
			})
		]);

		const model = buildAgentTranscriptModel(state);
		expect(model.final).toEqual({ content: 'the real answer', done: false });
	});

	it('keeps the model coherent across incremental folds (live SSE then reconnect backfill)', () => {
		let state = createAgentRunEventState();
		state = foldAgentRunEvent(
			state,
			agentRunEventFixture({ seq: 1, event_type: 'run.running', summary: 'Agent started' })
		);
		state = foldAgentRunEvent(
			state,
			agentRunEventFixture({
				seq: 2,
				event_type: 'text.delta',
				payload: {
					block_id: 'note-1',
					delta_index: 0,
					delta: 'Planning',
					block_kind: 'assistant_note'
				}
			})
		);
		state = foldAgentRunEvent(
			state,
			agentRunEventFixture({
				seq: 2,
				event_type: 'text.delta',
				payload: {
					block_id: 'note-1',
					delta_index: 0,
					delta: 'Planning',
					block_kind: 'assistant_note'
				}
			})
		);

		const model = buildAgentTranscriptModel(state);
		const textParts = model.parts.filter((part) => part.kind === 'assistant_note');
		expect(textParts).toHaveLength(1);
	});
});

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

	it('channels text.delta into textBlocks and never into finalText', () => {
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
					block_id: 'z-first',
					delta_index: 0,
					delta: 'Hello ',
					block_kind: 'assistant_note'
				},
				phase: 'running'
			})
		);
		state = foldAgentRunEvent(
			state,
			agentRunEventFixture({
				seq: 3,
				event_type: 'tool.requested',
				payload: { tool_name: 'web_search' },
				summary: 'Searching docs'
			})
		);
		state = foldAgentRunEvent(
			state,
			agentRunEventFixture({
				seq: 4,
				event_type: 'text.delta',
				payload: {
					block_id: 'a-second',
					delta_index: 0,
					delta: 'world',
					block_kind: 'action_summary'
				},
				phase: 'running'
			})
		);

		expect(state.finalText).toBe('');
		expect(state.items.map((item) => item.eventType)).toEqual(['run.running', 'tool.requested']);
		expect(state.runStatus).toBe('running');

		expect(
			state.textBlocks.map((block) => ({ id: block.id, kind: block.kind, text: block.text }))
		).toEqual([
			{ id: 'z-first', kind: 'assistant_note', text: 'Hello ' },
			{ id: 'a-second', kind: 'action_summary', text: 'world' }
		]);
	});

	it('accumulates text.delta chunks per block using delta_index order and dedupes replays', () => {
		let state = createAgentRunEventState();

		state = foldAgentRunEvent(
			state,
			agentRunEventFixture({
				seq: 1,
				event_type: 'text.delta',
				payload: {
					block_id: 'block-1',
					delta_index: 0,
					delta: 'Hello ',
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
					block_id: 'block-1',
					delta_index: 1,
					delta: 'world',
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
					block_id: 'block-1',
					delta_index: 1,
					delta: 'world',
					block_kind: 'assistant_note'
				}
			})
		);

		expect(state.textBlocks).toHaveLength(1);
		expect(state.textBlocks[0].text).toBe('Hello world');
		expect(state.textBlocks[0].status).toBe('running');
		expect(state.finalText).toBe('');
	});

	it('treats text.delta without block_kind as legacy transcript block but still keeps it out of finalText', () => {
		let state = createAgentRunEventState();

		state = foldAgentRunEvent(
			state,
			agentRunEventFixture({
				seq: 1,
				event_type: 'text.delta',
				payload: { block_id: 'legacy-1', delta_index: 0, delta: 'legacy note' }
			})
		);

		expect(state.finalText).toBe('');
		expect(state.textBlocks).toHaveLength(1);
		expect(state.textBlocks[0].kind).toBe('legacy');
		expect(state.textBlocks[0].text).toBe('legacy note');
	});

	it('strips unsafe reasoning/private fields from text.delta payloads before they enter transcript state', () => {
		let state = createAgentRunEventState();

		state = foldAgentRunEvent(
			state,
			agentRunEventFixture({
				seq: 1,
				event_type: 'text.delta',
				payload: {
					block_id: 'block-1',
					delta_index: 0,
					delta: 'visible summary',
					block_kind: 'assistant_note',
					chain_of_thought: 'hidden chain',
					raw_reasoning: 'hidden raw',
					reasoning: 'hidden reasoning',
					thought: 'hidden thought',
					debug: 'hidden debug',
					private: 'hidden private',
					raw: { massive: 'hidden payload' },
					nested: { chain_of_thought: 'hidden nested' }
				}
			})
		);

		expect(state.textBlocks[0].text).toBe('visible summary');
		const serialized = JSON.stringify(state);
		expect(serialized).not.toContain('hidden');
		expect(serialized).not.toContain('chain_of_thought');
		expect(serialized).not.toContain('reasoning');
		expect(serialized).not.toContain('thought');
		expect(serialized).not.toContain('debug');
		expect(serialized).not.toContain('private');
		expect(serialized).not.toContain('raw_reasoning');
	});

	it('surfaces concise details and strips raw reasoning fields', () => {
		const state = foldAgentRunEvents([
			agentRunEventFixture({
				seq: 1,
				event_type: 'action.summary',
				summary: 'Deciding next step',
				payload: {
					status: 'running',
					debug: 'hidden debug trace',
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

	it('derives lifecycle status from run and final phase events', () => {
		let state = createAgentRunEventState();

		state = foldAgentRunEvent(state, agentRunEventFixture({ seq: 1, event_type: 'run.queued' }));
		expect(state.runStatus).toBe('queued');

		state = foldAgentRunEvent(state, agentRunEventFixture({ seq: 2, event_type: 'run.running' }));
		expect(state.runStatus).toBe('running');

		state = foldAgentRunEvent(
			state,
			agentRunEventFixture({ seq: 3, event_type: 'approval.requested' })
		);
		expect(state.runStatus).toBe('waiting_approval');

		state = foldAgentRunEvent(
			state,
			agentRunEventFixture({ seq: 4, event_type: 'user_input.requested' })
		);
		expect(state.runStatus).toBe('waiting_user_input');

		state = foldAgentRunEvent(
			state,
			agentRunEventFixture({ seq: 5, event_type: 'user_input.completed' })
		);
		expect(state.runStatus).toBe('running');

		state = foldAgentRunEvent(state, agentRunEventFixture({ seq: 6, event_type: 'final.started' }));
		expect(state.runStatus).toBe('finalizing');

		state = foldAgentRunEvent(state, agentRunEventFixture({ seq: 7, event_type: 'run.completed' }));
		expect(state.runStatus).toBe('completed');
	});

	it('folds user input lifecycle as pending and terminal user_input items', () => {
		const state = foldAgentRunEvents([
			agentRunEventFixture({
				seq: 1,
				event_type: 'user_input.requested',
				summary: '',
				payload: {
					user_input_id: 'input-1',
					message: 'Which file should I update?',
					requested_schema: {
						type: 'object',
						properties: { file: { type: 'string', title: 'Target file' } },
						required: ['file']
					}
				}
			}),
			agentRunEventFixture({
				seq: 2,
				event_type: 'user_input.completed',
				summary: '',
				payload: {
					user_input_id: 'input-1',
					status: 'accepted',
					content: { file: 'README.md' }
				}
			})
		]);

		expect(state.runStatus).toBe('running');
		expect(state.counts.user_input).toBe(2);
		expect(state.items.map((item) => item.eventType)).toEqual([
			'user_input.requested',
			'user_input.completed'
		]);
		expect(state.items[0]).toMatchObject({
			category: 'user_input',
			label: 'User input',
			summary: 'Needs your input',
			status: 'running'
		});
		expect(state.items[1]).toMatchObject({
			category: 'user_input',
			label: 'User input',
			summary: 'User input submitted',
			status: 'done'
		});
	});

	it('marks declined user input as a normal terminal event', () => {
		const state = foldAgentRunEvents([
			agentRunEventFixture({
				seq: 1,
				event_type: 'user_input.declined',
				summary: '',
				payload: { user_input_id: 'input-1', status: 'declined' }
			})
		]);

		expect(state.items[0]).toMatchObject({
			category: 'user_input',
			status: 'done',
			summary: 'User input declined'
		});
	});

	it('exposes user-facing categories labels metadata and counts for Agent Run events', () => {
		const state = foldAgentRunEvents([
			agentRunEventFixture({
				seq: 1,
				event_type: 'model.selection.completed',
				summary: '',
				payload: { model_id: 'gpt-5.4', provider: 'openai' }
			}),
			agentRunEventFixture({
				seq: 2,
				event_type: 'tool.started',
				summary: '',
				payload: { tool_name: 'query_knowledge', status: 'running' }
			}),
			agentRunEventFixture({
				seq: 3,
				event_type: 'approval.requested',
				summary: '',
				payload: { approval_id: 'approval-1', action: 'overwrite report.txt' }
			}),
			agentRunEventFixture({
				seq: 4,
				event_type: 'artifact.registered',
				summary: '',
				payload: {
					artifact_id: 'artifact-1',
					name: 'report.txt',
					path: '/workspace/agent-runs/run-1/outputs/report.txt'
				}
			}),
			agentRunEventFixture({
				seq: 5,
				event_type: 'subagent.completed',
				participant_id: 'subagent-1',
				summary: '',
				payload: { participant_name: 'Citation checker', result_summary: 'Checked citations' }
			}),
			agentRunEventFixture({ seq: 6, event_type: 'final.started', summary: '' })
		]);

		expect(state.counts).toMatchObject({
			model: 1,
			tool: 1,
			approval: 1,
			artifact: 1,
			subagent: 1,
			final: 1
		});
		expect(state.items[0]).toMatchObject({
			category: 'model',
			label: 'Model',
			summary: 'Selected gpt-5.4',
			metadata: [{ label: 'Provider', value: 'openai' }]
		});
		expect(state.items[1]).toMatchObject({
			category: 'tool',
			label: 'Tool',
			summary: 'Running query_knowledge',
			metadata: [{ label: 'Status', value: 'running' }]
		});
		expect(state.items[2]).toMatchObject({
			category: 'approval',
			label: 'Approval',
			metadata: [{ label: 'Action', value: 'overwrite report.txt' }]
		});
		expect(state.items[3]).toMatchObject({
			category: 'artifact',
			label: 'Artifact',
			summary: 'Registered report.txt',
			metadata: [{ label: 'Path', value: '/workspace/agent-runs/run-1/outputs/report.txt' }]
		});
		expect(state.items[4]).toMatchObject({
			category: 'subagent',
			label: 'Subagent',
			summary: 'Completed Citation checker'
		});
		expect(state.items[5]).toMatchObject({
			category: 'final',
			label: 'Final answer'
		});
	});

	it('marks failed and cancelled terminal lifecycle states', () => {
		const failedState = foldAgentRunEvents([
			agentRunEventFixture({ seq: 1, event_type: 'run.running' }),
			agentRunEventFixture({ seq: 2, event_type: 'run.failed' })
		]);
		const cancelledState = foldAgentRunEvents([
			agentRunEventFixture({ seq: 1, event_type: 'run.running' }),
			agentRunEventFixture({ seq: 2, event_type: 'run.cancelled' })
		]);

		expect(failedState.runStatus).toBe('failed');
		expect(cancelledState.runStatus).toBe('cancelled');
	});
});

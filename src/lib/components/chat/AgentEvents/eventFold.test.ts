import { describe, expect, it } from 'vitest';

import {
	createAgentRunEventState,
	foldAgentRunEvent,
	foldAgentRunEvents
} from './eventFold';
import { agentRunEventFixture } from './fixtures';

describe('foldAgentRunEvents', () => {
	it('orders run events by seq and ignores duplicate or older events', () => {
		const state = foldAgentRunEvents([
			agentRunEventFixture({ seq: 3, event_type: 'tool.completed', summary: 'Search completed' }),
			agentRunEventFixture({ seq: 1, event_type: 'run.running', summary: 'Agent started' }),
			agentRunEventFixture({ seq: 2, event_type: 'tool.started', summary: 'Searching docs' }),
			agentRunEventFixture({ seq: 2, event_type: 'tool.started', summary: 'Duplicate should not render' }),
			agentRunEventFixture({ seq: 1, event_type: 'run.running', summary: 'Old replay should not render' })
		]);

		expect(state.lastSeq).toBe(3);
		expect(state.items.map((item) => item.summary)).toEqual([
			'Agent started',
			'Searching docs',
			'Search completed'
		]);
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
});

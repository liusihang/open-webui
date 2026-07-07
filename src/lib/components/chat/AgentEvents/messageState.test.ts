import { describe, expect, it } from 'vitest';

import { markAgentRunMessageDone } from './messageState';

describe('markAgentRunMessageDone', () => {
	it('marks an Agent Run assistant message done when the run reaches a terminal state', () => {
		const message = {
			id: 'assistant-1',
			role: 'assistant',
			done: false,
			agent_run_id: 'run-1'
		};

		const changed = markAgentRunMessageDone(message, 'completed');

		expect(changed).toBe(true);
		expect(message.done).toBe(true);
	});

	it('does not mark Agent Run assistant messages done while the run is still active', () => {
		const message = {
			id: 'assistant-1',
			role: 'assistant',
			done: false,
			agent_run_id: 'run-1'
		};

		const changed = markAgentRunMessageDone(message, 'running');

		expect(changed).toBe(false);
		expect(message.done).toBe(false);
	});
});

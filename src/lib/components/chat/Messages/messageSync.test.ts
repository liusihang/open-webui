import { describe, expect, it } from 'vitest';

import { shouldSyncRenderedMessage } from './messageSync';


describe('shouldSyncRenderedMessage', () => {
	it('stays false for unchanged message state', () => {
		const message = {
			content: 'hello',
			done: false,
			sources: [{ source: { id: 'a', name: 'A' }, metadata: [{ source: 'a' }], document: ['x'] }]
		};

		expect(shouldSyncRenderedMessage(message, { ...message })).toBe(false);
	});

	it('syncs when source count grows even if content is unchanged', () => {
		const current = {
			content: 'hello',
			done: false,
			sources: [{ source: { id: 'a', name: 'A' }, metadata: [{ source: 'a' }], document: ['x'] }]
		};
		const next = {
			...current,
			sources: [
				...current.sources,
				{ source: { id: 'b', name: 'B' }, metadata: [{ source: 'b' }], document: ['y'] }
			]
		};

		expect(shouldSyncRenderedMessage(current, next)).toBe(true);
	});

	it('syncs when tool output structure changes', () => {
		const current = {
			content: 'hello',
			done: false,
			output: [{ type: 'message', id: 'msg-1', status: 'in_progress' }]
		};
		const next = {
			...current,
			output: [
				...current.output,
				{ type: 'function_call', id: 'fc-1', call_id: 'fc-1', status: 'completed' }
			]
		};

		expect(shouldSyncRenderedMessage(current, next)).toBe(true);
	});

	it('syncs when status history appends a new status', () => {
		const current = {
			content: 'hello',
			done: false,
			statusHistory: [{ done: false, action: 'tool', description: 'Running' }]
		};
		const next = {
			...current,
			statusHistory: [
				...current.statusHistory,
				{ done: true, action: 'tool', description: 'Done' }
			]
		};

		expect(shouldSyncRenderedMessage(current, next)).toBe(true);
	});
});

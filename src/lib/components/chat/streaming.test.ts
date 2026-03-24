import { describe, expect, it } from 'vitest';

import {
	createStreamingTextState,
	drainStreamingTextState,
	getStreamingTextChunkSize,
	syncStreamingTextState
} from './streaming';

describe('chat streaming smoothing helpers', () => {
	it('queues only the newly appended suffix for monotonic streaming updates', () => {
		let state = createStreamingTextState('Hel');
		state = syncStreamingTextState(state, 'Hello');
		state = syncStreamingTextState(state, 'Hello world');

		expect(state.rendered).toBe('Hel');
		expect(state.target).toBe('Hello world');
		expect(state.queue).toBe('lo world');
	});

	it('drains queued text in bounded chunks and preserves the remaining backlog', () => {
		const drained = drainStreamingTextState(
			{
				rendered: 'Hello',
				target: 'Hello wonderful world',
				queue: ' wonderful world'
			},
			6
		);

		expect(drained.rendered).toBe('Hello wonde');
		expect(drained.queue).toBe('rful world');
		expect(drained.target).toBe('Hello wonderful world');
	});

	it('snaps immediately when the next content is not a prefix extension', () => {
		const reset = syncStreamingTextState(
			{
				rendered: 'Hello',
				target: 'Hello world',
				queue: ' world'
			},
			'<details>Thinking</details>\nHello world'
		);

		expect(reset).toEqual(
			createStreamingTextState('<details>Thinking</details>\nHello world')
		);
	});

	it('accelerates draining when the queue grows', () => {
		expect(getStreamingTextChunkSize(1)).toBe(6);
		expect(getStreamingTextChunkSize(36)).toBe(6);
		expect(getStreamingTextChunkSize(120)).toBe(20);
		expect(getStreamingTextChunkSize(500)).toBe(32);
	});
});

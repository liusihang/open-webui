import { describe, expect, it } from 'vitest';

import {
	appendChannelMessage,
	replaceChannelMessage,
	shouldAutoScrollOnMessageUpdate
} from './streaming';

describe('channel streaming helpers', () => {
	it('updates one channel bubble in place while streaming', () => {
		const placeholder = {
			id: 'm-assistant',
			content: '',
			meta: { streaming: true, done: false }
		};

		let messages = appendChannelMessage([], placeholder);
		messages = replaceChannelMessage(messages, {
			id: 'm-assistant',
			content: 'Hel',
			meta: { streaming: true, done: false }
		});
		messages = replaceChannelMessage(messages, {
			id: 'm-assistant',
			content: 'Hello',
			meta: { streaming: false, done: true }
		});

		expect(messages).toHaveLength(1);
		expect(messages[0].content).toBe('Hello');
		expect(messages[0].meta).toEqual({ streaming: false, done: true });
	});

	it('replaces a temp message with the persisted placeholder only once', () => {
		const tempMessage = {
			id: 'temp-1',
			temp_id: 'temp-1',
			content: 'hi'
		};

		const messages = appendChannelMessage([tempMessage], {
			id: 'server-1',
			temp_id: 'temp-1',
			content: 'hi'
		});

		expect(messages).toHaveLength(1);
		expect(messages[0].id).toBe('server-1');
	});

	it('keeps auto-scroll pinned only when already near the bottom', () => {
		expect(
			shouldAutoScrollOnMessageUpdate({
				scrollEnd: true,
				nextMessage: { id: 'm1', meta: { streaming: true, done: false } }
			})
		).toBe(true);

		expect(
			shouldAutoScrollOnMessageUpdate({
				scrollEnd: false,
				nextMessage: { id: 'm1', meta: { streaming: true, done: false } }
			})
		).toBe(false);
	});
});

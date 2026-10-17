import { afterEach, describe, expect, it, vi } from 'vitest';
import { createMessageStateSaver, type MessageStateSnapshot } from './persistence';

afterEach(() => {
	vi.useRealTimers();
});
const snapshot = (): MessageStateSnapshot => ({
	chatId: 'old-chat',
	messageId: 'answer',
	token: 'test-token',
	state: {
		version: 1,
		blocks: { first: { amount: 20 } }
	}
});

describe('interaction state persistence', () => {
	it('flushes the original chat and captured metadata when navigation precedes debounce', async () => {
		vi.useFakeTimers();
		const save = vi.fn(async () => {});
		const saver = createMessageStateSaver(save, vi.fn());
		const current = snapshot();
		saver.schedule(current);
		current.chatId = 'new-chat';
		current.messageId = 'different';
		current.state = { version: 1, blocks: {} };
		await saver.flush();
		expect(save).toHaveBeenCalledWith(snapshot());
		await vi.runAllTimersAsync();
		expect(save).toHaveBeenCalledTimes(1);
	});
	it('debounces movement to the final immutable state', async () => {
		vi.useFakeTimers();
		const save = vi.fn(async () => {});
		const saver = createMessageStateSaver(save, vi.fn());
		const first = snapshot();
		saver.schedule(first);
		const final = snapshot();
		final.state = { version: 1, blocks: { first: { amount: 70 } } };
		saver.schedule(final);
		await vi.advanceTimersByTimeAsync(350);
		expect(save).toHaveBeenCalledTimes(1);
		expect(save).toHaveBeenCalledWith(final);
	});
	it('serializes saves so an older request cannot overwrite a newer value', async () => {
		let finish!: () => void;
		const order: number[] = [];
		const save = vi.fn(async (value: MessageStateSnapshot) => {
			const number = value.state.blocks.first.amount as number;
			if (number === 1) await new Promise<void>((resolve) => (finish = resolve));
			order.push(number);
		});
		const saver = createMessageStateSaver(save, vi.fn());
		saver.schedule({ ...snapshot(), state: { version: 1, blocks: { first: { amount: 1 } } } });
		const first = saver.flush();
		await Promise.resolve();
		saver.schedule({ ...snapshot(), state: { version: 1, blocks: { first: { amount: 2 } } } });
		const second = saver.flush();
		expect(save).toHaveBeenCalledTimes(1);
		finish();
		await first;
		await second;
		expect(order).toEqual([1, 2]);
	});
	it('reports a failed save and allows the next update', async () => {
		const error = vi.fn();
		const save = vi
			.fn()
			.mockRejectedValueOnce(new Error('offline'))
			.mockResolvedValueOnce(undefined);
		const saver = createMessageStateSaver(save, error);
		saver.schedule(snapshot());
		await saver.flush();
		saver.schedule(snapshot());
		await saver.flush();
		expect(error).toHaveBeenCalledTimes(1);
		expect(save).toHaveBeenCalledTimes(2);
	});
});

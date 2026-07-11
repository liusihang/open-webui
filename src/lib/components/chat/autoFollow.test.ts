import { describe, expect, it, vi } from 'vitest';

import { autoFollowResize, createAutoFollowFrameScheduler } from './autoFollow';

describe('autoFollowResize', () => {
	it('follows content growth only while the user remains at the bottom', () => {
		let resizeCallback: ResizeObserverCallback | null = null;
		const observed: Element[] = [];
		const disconnect = vi.fn();
		const scheduleScroll = vi.fn();
		let shouldFollow = true;

		class FakeResizeObserver {
			constructor(callback: ResizeObserverCallback) {
				resizeCallback = callback;
			}

			observe(target: Element) {
				observed.push(target);
			}

			disconnect() {
				disconnect();
			}
		}

		const target = {} as Element;
		const action = autoFollowResize(target, {
			shouldFollow: () => shouldFollow,
			scheduleScroll,
			ResizeObserverClass: FakeResizeObserver
		});

		expect(observed).toEqual([target]);
		expect(resizeCallback).not.toBeNull();

		resizeCallback?.([], {} as ResizeObserver);
		expect(scheduleScroll).toHaveBeenCalledTimes(1);

		shouldFollow = false;
		resizeCallback?.([], {} as ResizeObserver);
		expect(scheduleScroll).toHaveBeenCalledTimes(1);

		shouldFollow = true;
		resizeCallback?.([], {} as ResizeObserver);
		expect(scheduleScroll).toHaveBeenCalledTimes(2);

		action.destroy();
		expect(disconnect).toHaveBeenCalledTimes(1);
	});

	it('rechecks follow intent and target identity on every queued frame', () => {
		let shouldFollow = true;
		let target = { id: 'first' };
		let nextFrameId = 1;
		const queued = new Map<number, FrameRequestCallback>();
		const cancelled: number[] = [];
		const scroll = vi.fn();

		const requestFrame = (callback: FrameRequestCallback) => {
			const frameId = nextFrameId++;
			queued.set(frameId, callback);
			return frameId;
		};
		const cancelFrame = (frameId: number) => {
			cancelled.push(frameId);
			queued.delete(frameId);
		};
		const flushNextFrame = () => {
			const entry = queued.entries().next().value as [number, FrameRequestCallback] | undefined;
			if (!entry) return;
			queued.delete(entry[0]);
			entry[1](0);
		};

		const scheduler = createAutoFollowFrameScheduler({
			shouldFollow: () => shouldFollow,
			getTarget: () => target,
			scroll,
			requestFrame,
			cancelFrame,
			frameCount: 3
		});

		scheduler.schedule();
		expect(queued.size).toBe(1);
		flushNextFrame();
		expect(scroll).toHaveBeenCalledTimes(1);
		expect(scroll).toHaveBeenLastCalledWith(target);

		shouldFollow = false;
		flushNextFrame();
		expect(scroll).toHaveBeenCalledTimes(1);
		expect(queued.size).toBe(0);

		shouldFollow = true;
		scheduler.schedule();
		target = { id: 'second' };
		flushNextFrame();
		expect(scroll).toHaveBeenCalledTimes(1);
		expect(queued.size).toBe(0);

		scheduler.schedule();
		const pendingFrame = [...queued.keys()][0];
		scheduler.destroy();
		expect(cancelled).toContain(pendingFrame);
		expect(queued.size).toBe(0);
	});
});

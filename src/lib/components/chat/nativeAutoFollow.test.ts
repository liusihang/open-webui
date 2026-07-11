import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

import { armNativeScrollAnchor } from './nativeAutoFollow';

describe('native transcript auto-follow layout', () => {
	it('keeps message wrappers in normal flow and owns a bottom scroll anchor', () => {
		const chatSource = readFileSync('src/lib/components/chat/Chat.svelte', 'utf8');
		const messagesSource = readFileSync('src/lib/components/chat/Messages.svelte', 'utf8');
		const nativeSource = readFileSync('src/lib/components/chat/nativeAutoFollow.ts', 'utf8');

		expect(chatSource).toContain('class="min-h-full w-full flex flex-col flex-none"');
		expect(chatSource).toContain('class:native-auto-follow={autoScroll}');
		expect(messagesSource).toContain("export let className = 'min-h-full flex pt-8 flex-none';");
		expect(messagesSource).toContain('<div class="w-full pt-2 flex-none">');
		expect(messagesSource).toContain('class="chat-scroll-anchor"');
		expect(messagesSource).toContain('use:nativeScrollAnchor={autoScroll}');
		expect(messagesSource).toContain("#messages-container.native-auto-follow [role='log']");
		expect(messagesSource).toContain('overflow-anchor: none');
		expect(messagesSource).toContain('overflow-anchor: auto');
		expect(nativeSource).toContain('armNativeScrollAnchor({');

		expect(chatSource).not.toContain("from './autoFollow'");
		expect(messagesSource).not.toContain("from './autoFollow'");
		expect(messagesSource).not.toContain('on:contentresize');
		expect(messagesSource).not.toContain('onMount(() =>');
		expect(chatSource).not.toContain('scheduleScrollToBottom');
		expect(nativeSource).toContain("node.closest('#messages-container')");
	});

	it('arms the native anchor once across the initial layout frames', () => {
		const container = { scrollTop: 0, scrollHeight: 240 };
		let nextFrameId = 1;
		const frames = new Map<number, FrameRequestCallback>();
		const cancelled: number[] = [];
		const requestFrame = (callback: FrameRequestCallback) => {
			const id = nextFrameId++;
			frames.set(id, callback);
			return id;
		};
		const flushFrame = () => {
			const entry = frames.entries().next().value as [number, FrameRequestCallback] | undefined;
			if (!entry) return;
			frames.delete(entry[0]);
			entry[1](0);
		};

		const cancel = armNativeScrollAnchor({
			shouldFollow: () => true,
			getContainer: () => container,
			requestFrame,
			cancelFrame: (id) => {
				cancelled.push(id);
				frames.delete(id);
			},
			frameCount: 3
		});

		expect(frames.size).toBe(1);
		flushFrame();
		expect(container.scrollTop).toBe(240);
		container.scrollHeight = 320;
		flushFrame();
		expect(container.scrollTop).toBe(320);
		container.scrollHeight = 400;
		flushFrame();
		expect(container.scrollTop).toBe(400);
		expect(frames.size).toBe(0);

		cancel();
		expect(cancelled).toEqual([]);
	});

	it('stops the remaining initial frames when follow mode is disabled', () => {
		const container = { scrollTop: 0, scrollHeight: 240 };
		let following = true;
		let nextFrameId = 1;
		const frames = new Map<number, FrameRequestCallback>();
		const requestFrame = (callback: FrameRequestCallback) => {
			const id = nextFrameId++;
			frames.set(id, callback);
			return id;
		};
		const flushFrame = () => {
			const entry = frames.entries().next().value as [number, FrameRequestCallback] | undefined;
			if (!entry) return;
			frames.delete(entry[0]);
			entry[1](0);
		};

		armNativeScrollAnchor({
			shouldFollow: () => following,
			getContainer: () => container,
			requestFrame,
			cancelFrame: (id) => frames.delete(id),
			frameCount: 3
		});

		flushFrame();
		expect(container.scrollTop).toBe(240);
		following = false;
		container.scrollHeight = 400;
		flushFrame();
		expect(container.scrollTop).toBe(240);
		expect(frames.size).toBe(0);
	});

	it('cancels a pending initial frame when the sentinel is destroyed', () => {
		const container = { scrollTop: 0, scrollHeight: 240 };
		const frames = new Map<number, FrameRequestCallback>();
		const cancelled: number[] = [];
		const cancel = armNativeScrollAnchor({
			shouldFollow: () => true,
			getContainer: () => container,
			requestFrame: (callback) => {
				frames.set(1, callback);
				return 1;
			},
			cancelFrame: (id) => {
				cancelled.push(id);
				frames.delete(id);
			}
		});

		cancel();
		expect(cancelled).toEqual([1]);
		expect(frames.size).toBe(0);
		expect(container.scrollTop).toBe(0);
	});
});

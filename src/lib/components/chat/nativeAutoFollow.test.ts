import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

describe('native transcript auto-follow layout', () => {
	it('keeps message wrappers in normal flow and owns a bottom scroll anchor', () => {
		const chatSource = readFileSync('src/lib/components/chat/Chat.svelte', 'utf8');
		const messagesSource = readFileSync('src/lib/components/chat/Messages.svelte', 'utf8');

		expect(chatSource).toContain('class="min-h-full w-full flex flex-col flex-none"');
		expect(chatSource).toContain('class:native-auto-follow={autoScroll}');
		expect(messagesSource).toContain("export let className = 'min-h-full flex pt-8 flex-none';");
		expect(messagesSource).toContain('<div class="w-full pt-2 flex-none">');
		expect(messagesSource).toContain('class="chat-scroll-anchor"');
		expect(messagesSource).toContain("#messages-container.native-auto-follow [role='log']");
		expect(messagesSource).toContain('overflow-anchor: none');
		expect(messagesSource).toContain('overflow-anchor: auto');

		expect(chatSource).not.toContain("from './autoFollow'");
		expect(messagesSource).not.toContain("from './autoFollow'");
		expect(messagesSource).not.toContain('on:contentresize');
		expect(chatSource).not.toContain('scheduleScrollToBottom');
	});
});

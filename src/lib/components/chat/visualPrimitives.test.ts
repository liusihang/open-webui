import { describe, expect, it } from 'vitest';

import {
	chatActionButtonClass,
	chatBadgeClass,
	chatCollapsibleHeaderClass,
	chatMonoBlockClass,
	chatMutedSurfaceClass,
	chatStatusDotClass,
	chatTerminalClass,
	resolveChatVisualTone
} from './visualPrimitives';

describe('chat visual primitives', () => {
	it('maps agent and AI Elements-like states to low-noise visual tones', () => {
		expect(resolveChatVisualTone('input-available')).toBe('running');
		expect(resolveChatVisualTone('output-available')).toBe('success');
		expect(resolveChatVisualTone('approval-requested')).toBe('warning');
		expect(resolveChatVisualTone('output-error')).toBe('danger');
		expect(resolveChatVisualTone('unknown-state')).toBe('muted');
		expect(resolveChatVisualTone(null)).toBe('muted');
	});

	it('keeps compact badge and status dot classes within the chat foundation vocabulary', () => {
		expect(chatBadgeClass('approval-requested')).toContain('text-[11px]');
		expect(chatBadgeClass('approval-requested')).toContain('rounded-md');
		expect(chatBadgeClass('approval-requested')).toContain('amber');
		expect(chatStatusDotClass('running')).toContain('size-1.5');
		expect(chatStatusDotClass('running')).toContain('animate-pulse');
		expect(chatStatusDotClass('output-available')).toContain('green');
	});

	it('exports shared surface, action, disclosure, mono, and terminal treatments', () => {
		const joined = [
			chatMutedSurfaceClass,
			chatActionButtonClass,
			chatCollapsibleHeaderClass,
			chatMonoBlockClass,
			chatTerminalClass
		].join(' ');

		expect(chatMutedSurfaceClass).toContain('border');
		expect(chatMutedSurfaceClass).toContain('rounded-lg');
		expect(chatActionButtonClass).toContain('hover:bg-gray-100');
		expect(chatCollapsibleHeaderClass).toContain('text-left');
		expect(chatMonoBlockClass).toContain('font-mono');
		expect(chatTerminalClass).toContain('bg-gray-950');
		expect(joined).not.toMatch(/rounded-(xl|2xl|3xl|full)/);
	});
});

import { describe, expect, it } from 'vitest';

import { getToolCallIframePolicy } from './toolCallIframePolicy';

describe('getToolCallIframePolicy', () => {
	it('always enables forms for interactive tool call embeds', () => {
		expect(
			getToolCallIframePolicy({
				iframeSandboxAllowForms: false,
				iframeSandboxAllowSameOrigin: false
			})
		).toEqual({
			allowForms: true,
			allowSameOrigin: false
		});
	});

	it('preserves same-origin opt-in from user settings', () => {
		expect(
			getToolCallIframePolicy({
				iframeSandboxAllowForms: false,
				iframeSandboxAllowSameOrigin: true
			})
		).toEqual({
			allowForms: true,
			allowSameOrigin: true
		});
	});
});

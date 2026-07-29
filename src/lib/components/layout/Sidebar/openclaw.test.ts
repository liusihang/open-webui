import { existsSync, readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

describe('openclaw sidebar removal', () => {
	it('omits the fixed OpenClaw quick entry and its resolver', () => {
		const sidebarSource = readFileSync('src/lib/components/layout/Sidebar.svelte', 'utf-8');

		expect(sidebarSource).not.toContain('id="sidebar-openclaw-button"');
		expect(sidebarSource).not.toContain('🦞');
		expect(sidebarSource).not.toContain('OPENCLAW_LABEL');
		expect(existsSync('src/lib/components/layout/Sidebar/openclaw.ts')).toBe(false);
	});
});

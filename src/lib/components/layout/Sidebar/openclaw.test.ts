import { readFileSync } from 'node:fs';

import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('$lib/apis/channels', () => ({
	getChannels: vi.fn()
}));

describe('openclaw sidebar replay', () => {
	beforeEach(async () => {
		vi.resetModules();
		const { getChannels } = await import('$lib/apis/channels');
		vi.mocked(getChannels).mockReset();
	});

	it('renders a fixed openclaw sidebar entry with the lobster brand icon', () => {
		const sidebarSource = readFileSync('src/lib/components/layout/Sidebar.svelte', 'utf-8');

		expect(sidebarSource).toContain('id="sidebar-openclaw-button"');
		expect(sidebarSource).toContain('🦞');
		expect(sidebarSource).toContain('OPENCLAW_LABEL');
	});

	it('resolves the current user openclaw channel once and reuses it', async () => {
		const { getChannels } = await import('$lib/apis/channels');
		vi.mocked(getChannels).mockResolvedValue([
			{ id: 'group-1', type: 'group', name: 'General' },
			{ id: 'openclaw-123', type: 'openclaw', name: 'OpenClaw' }
		]);

		const { resolveOpenClawChannelId, resetOpenClawChannelResolver } = await import('./openclaw');
		resetOpenClawChannelResolver();

		const first = await resolveOpenClawChannelId('token-1');
		const second = await resolveOpenClawChannelId('token-1');

		expect(first).toBe('openclaw-123');
		expect(second).toBe('openclaw-123');
		expect(getChannels).toHaveBeenCalledTimes(1);
		expect(getChannels).toHaveBeenCalledWith('token-1');
	});
});

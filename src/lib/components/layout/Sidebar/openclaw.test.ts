import { readFileSync } from 'node:fs';

import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('$lib/apis/channels', () => ({
	getOpenClawMe: vi.fn()
}));

import { getOpenClawMe } from '$lib/apis/channels';
import { resolveOpenClawChannelId, resetOpenClawChannelResolver } from './openclaw';

describe('openclaw sidebar helper', () => {
	beforeEach(() => {
		resetOpenClawChannelResolver();
		vi.mocked(getOpenClawMe).mockReset();
	});

	it('resolves the current user openclaw channel once and reuses it', async () => {
		vi.mocked(getOpenClawMe).mockResolvedValue({
			id: 'openclaw-123',
			type: 'openclaw',
			user_id: 'user-1',
			name: 'openclaw'
		});

		const first = await resolveOpenClawChannelId('token-1');
		const second = await resolveOpenClawChannelId('token-1');

		expect(first).toBe('openclaw-123');
		expect(second).toBe('openclaw-123');
		expect(getOpenClawMe).toHaveBeenCalledTimes(1);
		expect(getOpenClawMe).toHaveBeenCalledWith('token-1');
	});

	it('renders the sidebar entry with the lobster icon', () => {
		const sidebarSource = readFileSync('src/lib/components/layout/Sidebar.svelte', 'utf-8');
		const lobsterBadges = sidebarSource.match(/>\s*🦞\s*</g) ?? [];
		const ocBadges = sidebarSource.match(/>\s*OC\s*</g) ?? [];

		expect(sidebarSource).toContain('🦞');
		expect(lobsterBadges).toHaveLength(2);
		expect(ocBadges).toHaveLength(0);
	});
});

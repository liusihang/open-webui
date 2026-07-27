import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';

import * as configs from './index';

describe('conversation mode profile administrator API', () => {
	it('declares public, private, tri-state, revision, warning, and error contracts', () => {
		const source = readFileSync(new URL('./index.ts', import.meta.url), 'utf8');

		expect(source).toContain("export type ConversationMode = 'chat' | 'agent'");
		expect(source).toContain("export type ConversationModeProfileDefault<T> = 'inherit' | T");
		expect(source).toContain('ConversationModeProfilePublic');
		expect(source).toContain('ConversationModeProfileRevision');
		expect(source).toContain('content_hash');
		expect(source).toContain('ModeProfileApiError');
	});

	it('uses expected current revision IDs for save and restore', async () => {
		const fetchMock = vi.fn().mockResolvedValue({
			ok: true,
			json: async () => ({ revision_id: 'chat-r2' })
		});
		vi.stubGlobal('fetch', fetchMock);

		const profile = {
			schema_version: 1,
			system_prompt: '',
			defaults: {
				terminal_id: 'inherit' as const,
				tool_ids: [],
				skill_ids: 'inherit' as const,
				filter_ids: 'inherit' as const,
				feature_ids: ['web_search']
			}
		};

		await expect(
			(configs as Record<string, any>).saveConversationModeProfile(
				'token',
				'chat',
				'chat-r1',
				profile
			)
		).resolves.toEqual({ revision_id: 'chat-r2' });
		await expect(
			(configs as Record<string, any>).restoreConversationModeProfile(
				'token',
				'chat',
				'chat-r0',
				'chat-r1'
			)
		).resolves.toEqual({ revision_id: 'chat-r2' });

		expect(fetchMock.mock.calls.map((call) => JSON.parse(call[1].body))).toEqual([
			{ expected_current_revision_id: 'chat-r1', profile },
			{ expected_current_revision_id: 'chat-r1' }
		]);
	});

	it('keeps the public profile contract free of the private administrator prompt', () => {
		const source = readFileSync(new URL('./index.ts', import.meta.url), 'utf8');
		const publicContract = source.match(
			/export type ConversationModeProfilePublic = \{([\s\S]*?)\n\};/
		);

		expect(publicContract?.[1] ?? '').not.toContain('system_prompt');
		expect(publicContract?.[1] ?? '').not.toContain('created_by');
	});
});

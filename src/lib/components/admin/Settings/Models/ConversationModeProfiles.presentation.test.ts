import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import type { ConversationModeProfileRevision } from '$lib/apis/configs';
import { catalogItems, detailPresentation, modeForTabKey } from './conversationModeProfileState';

const revision: ConversationModeProfileRevision = {
	revision_id: 'revision-2',
	mode: 'chat',
	revision_number: 2,
	schema_version: 1,
	created_at: 0,
	created_by: 'admin',
	restored_from_revision_id: 'revision-1',
	is_current: true,
	content_hash: '1234567890abcdef',
	system_prompt: 'Private administrator prompt',
	defaults: {
		terminal_id: null,
		tool_ids: ['tool-a'],
		feature_ids: ['web_search']
	},
	warnings: []
};

describe('conversation mode profile presentation model', () => {
	const profilesSource = () =>
		readFileSync(new URL('./ConversationModeProfiles.svelte', import.meta.url), 'utf8');

	it('provides the fetched private detail, defaults, hash state, and restore origin for the local detail panel', () => {
		const detail = detailPresentation(revision);

		expect(detail.systemPrompt).toBe('Private administrator prompt');
		expect(detail.defaults).toEqual(
			expect.arrayContaining([
				'Terminal: Disabled',
				'Tools: Override (tool-a)',
				'Feature defaults: Override (Web Search)'
			])
		);
		expect(detail.metadata).toEqual(
			expect.arrayContaining(['Content hash: 1234567890ab…', 'Restored from: revision-1'])
		);
	});

	it('keeps inactive selected resources visible but disabled and maps all required tab keys', () => {
		const items = catalogItems(
			[
				{ id: 'tool-a', name: 'Current tool', is_active: true },
				{ id: 'tool-old', name: 'Old tool', is_active: false }
			],
			['tool-old']
		);

		expect(items.map((item) => [item.label, item.disabled])).toEqual([
			['Current tool', false],
			['Old tool (inactive)', true]
		]);
		expect(modeForTabKey('chat', 'ArrowRight')).toBe('agent');
		expect(modeForTabKey('agent', 'ArrowLeft')).toBe('chat');
		expect(modeForTabKey('agent', 'Home')).toBe('chat');
		expect(modeForTabKey('chat', 'End')).toBe('agent');
	});

	it('preserves mode-scoped feedback across tab selection and renders complete accessible tabs', () => {
		const source = profilesSource();

		expect(source).not.toContain('controller.clearFeedback(mode);');
		expect(source).toContain('id="conversation-mode-panel-{item.mode}"');
		expect(source).toContain('hidden={activeMode !== item.mode}');
		expect(source).toContain('role="alert"');
		expect(source).toContain('modeForTabKey(activeMode, event.key)');
	});
});

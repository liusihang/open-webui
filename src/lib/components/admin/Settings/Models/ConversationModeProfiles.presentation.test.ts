import { existsSync, readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const readSource = (name: string) => {
	const path = new URL(`./${name}`, import.meta.url);
	return existsSync(path) ? readFileSync(path, 'utf8') : '';
};

describe('conversation mode profile presentation contract', () => {
	it('adds a clear Models entry without replacing the ordinary model list', () => {
		const models = readFileSync(new URL('../Models.svelte', import.meta.url), 'utf8');

		expect(models).toContain(
			"import ConversationModeProfiles from './Models/ConversationModeProfiles.svelte'"
		);
		expect(models).toContain('<ConversationModeProfiles />');
		expect(models).toContain('id="model-list"');
	});

	it('has separate Chat and Agent administrator templates with no model or reasoning controls', () => {
		const source = readSource('ConversationModeProfiles.svelte');
		const editor = readSource('ConversationModeProfileEditor.svelte');

		expect(source).toContain("mode: 'chat'");
		expect(source).toContain("mode: 'agent'");
		expect(editor).toContain('Enforced System Prompt');
		expect(editor).toContain('Inherit');
		expect(editor).toContain('Override');
		expect(editor).toContain('Terminal');
		expect(editor).toContain('Tools');
		expect(editor).toContain('Skills');
		expect(editor).toContain('Filters');
		expect(editor).toContain('Web Search');
		expect(editor).toContain('Code Interpreter');
		expect(editor).toContain('Image Generation');
		expect(editor).not.toContain('ModelSelector');
		expect(editor).not.toContain('Reasoning Depth');
	});

	it('shows revision metadata, validation states, conflict refresh, and restore-as-new-revision', () => {
		const source = readSource('ConversationModeProfiles.svelte');
		const editor = readSource('ConversationModeProfileEditor.svelte');

		expect(source).toContain('Revision');
		expect(source).toContain('Content hash');
		expect(editor).toContain('Unsaved changes');
		expect(editor).toContain('Validation errors');
		expect(editor).toContain('Warnings');
		expect(source).toContain('refreshAfterConflict');
		expect(source).toContain('Restore as new revision');
		expect(source).toContain('expectedCurrentRevisionId');
	});

	it('does not put administrator prompt content into ordinary public stores', () => {
		const stores = readFileSync(new URL('../../../../stores/index.ts', import.meta.url), 'utf8');
		const editor = readSource('ConversationModeProfileEditor.svelte');

		expect(stores).not.toContain('modeProfileSystemPrompt');
		expect(editor).not.toContain("from '$lib/stores'");
		expect(editor).not.toContain('console.');
	});

	it('treats omitted backend defaults as inherit rather than an empty override', () => {
		const editor = readSource('ConversationModeProfileEditor.svelte');

		expect(editor).toContain("defaults.terminal_id ?? 'inherit'");
		expect(editor).toContain("defaults.tool_ids ?? 'inherit'");
		expect(editor).toContain("defaults.feature_ids ?? 'inherit'");
	});
});

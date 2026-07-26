import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const readSource = (relativePath: string) =>
	readFileSync(new URL(relativePath, import.meta.url), 'utf8');

describe('conversation mode presentation contract', () => {
	it('renders an accessible Chat and Agent selector with locked new-chat behavior', () => {
		const selector = readSource('./ConversationModeSelector.svelte');

		expect(selector).toContain('role="radiogroup"');
		expect(selector).toContain('role="radio"');
		expect(selector).toContain("value: 'chat'");
		expect(selector).toContain("value: 'agent'");
		expect(selector).toContain('aria-checked={mode === option.value}');
		expect(selector).toContain('onSelect(nextMode)');
		expect(selector).toContain('onCreateNew(nextMode)');
		expect(selector).toContain("nextMode === 'agent' && !agentAvailable");
	});

	it('centers the selector independently from the model and action controls', () => {
		const navbar = readSource('./Navbar.svelte');

		expect(navbar).toContain(
			"import ConversationModeSelector from './ConversationModeSelector.svelte'"
		);
		expect(navbar).toContain('<ConversationModeSelector');
		expect(navbar).toContain('absolute left-1/2');
		expect(navbar).toContain('{conversationMode}');
		expect(navbar).toContain('locked={conversationModeLocked}');
	});

	it('keeps mode server-backed across load, requests, drafts, and saved chats', () => {
		const chat = readSource('./Chat.svelte');

		expect(chat).toContain("let conversationMode: ConversationMode = 'chat'");
		expect(chat).toContain('normalizeConversationMode(chatContent?.mode)');
		expect(chat).toContain('chat_mode: conversationMode');
		expect(chat).toContain('mode: conversationMode');
		expect(chat).toContain('conversationMode: conversationMode');
		expect(chat).toMatch(
			/resolveConversationModeRequestModels\([\s\S]*selectedModelIds,[\s\S]*conversationMode[\s\S]*\)/
		);
		expect(chat).toMatch(
			/buildModelReasoningPayload\([\s\S]*model,[\s\S]*reasoningEffort[\s\S]*\)/
		);
		expect(chat).toContain('...(reasoning ? { reasoning } : {})');
		expect(chat).toContain('conversationModeLocked');
		expect(chat).toContain('pendingConversationMode');
		expect(chat).not.toContain('selectedModels = resolvedAgentModels');
		expect(chat).not.toContain("requestedMode === 'agent' && !isAgentModeCapabilityEnabled");
	});

	it('uses the shared composer reasoning-effort control in both modes', () => {
		const input = readSource('./MessageInput.svelte');

		expect(input).toContain('<ComposerModelSettings');
		expect(input).toContain('bind:reasoningEffort');
		expect(input).not.toContain('aria-label="思考深度"');
		expect(input).not.toContain("{#if conversationMode === 'agent'}");
	});

	it('preserves a valid reasoning effort when the selected model changes', () => {
		const chat = readSource('./Chat.svelte');
		const resetInputBody = chat.match(/const resetInput = async \(\) => \{([\s\S]*?)\n\t\};/)?.[1];

		expect(resetInputBody).toBeDefined();
		expect(resetInputBody).not.toContain("reasoningEffort = 'medium'");
	});

	it('keeps unavailable Agent conversations readable but disables their composer', () => {
		const chat = readSource('./Chat.svelte');

		expect(chat).toContain(
			"agentConversationUnavailable = conversationMode === 'agent' && !agentModeAvailable"
		);
		expect(chat).toContain("$i18n.t('Agent Mode is currently unavailable')");
		expect(chat).toMatch(
			/\{:else if agentConversationUnavailable\}[\s\S]*Agent Mode is currently unavailable/
		);
	});
});

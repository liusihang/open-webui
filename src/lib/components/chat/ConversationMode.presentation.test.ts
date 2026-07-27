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
		const resetInputBody = chat.match(
			/const resetInput = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};/
		)?.[1];

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

	it('integrates public mode-profile revisions and capability selections without Prompt or reasoning state', () => {
		const chat = readSource('./Chat.svelte');

		expect(chat).toContain("from '$lib/components/chat/conversationModeProfiles'");
		expect(chat).toContain('conversation_mode_profiles');
		expect(chat).toContain('modeProfileRevisionId');
		expect(chat).toContain('mode_profile_revision_id: modeProfileRevisionId');
		expect(chat).toContain('applyModeProfileInitialization');
		expect(chat).toContain('applyModeProfileModelChange');
		expect(chat).toContain('selectedTerminalId.set(null)');
		expect(chat).toContain('serializeConversationModeCapabilityRequest');
		expect(chat).toContain('...capabilityRequest.request');
		expect(chat).not.toContain('modeProfileSystemPrompt');
	});

	it('uses one explicit authority state for existing chats and request serialization', () => {
		const chat = readSource('./Chat.svelte');
		const navigateHandler = chat.match(
			/const navigateHandler = async \(\) => \{([\s\S]*?)\n\t\};\n\n\tconst onSelect/
		)?.[1];

		expect(chat).toContain('isDirectToolServersPermitted');
		expect(chat).toContain('createConversationModeCapabilityAuthorityController');
		expect(chat).toContain('serializeConversationModeCapabilityRequest');
		expect(chat).toContain('existingChat: true');
		expect(chat).toContain("modeProfileCapabilityAuthority === 'inherit_bound'");
		expect(navigateHandler).toBeDefined();
		expect(navigateHandler).not.toContain('await setDefaults()');
		expect(chat).not.toContain('modeProfileBoundWithoutDraft');
		expect(chat).not.toContain('modeProfileCapabilitiesOverridden');
	});

	it('persists authority in both drafts and tracks only capability changes', () => {
		const chat = readSource('./Chat.svelte');
		const observations =
			chat.match(/modeProfileCapabilityAuthorityController\.observe\(data\)/g) ?? [];
		const persistedMarkers =
			chat.match(
				/modeProfileCapabilityAuthority:\s*modeProfileCapabilityAuthorityController\.snapshot\(\)/g
			) ?? [];

		expect(chat).toContain('createConversationModeCapabilityAuthorityController');
		expect(observations).toHaveLength(2);
		expect(persistedMarkers).toHaveLength(2);
		expect(chat).toContain('modeProfileCapabilityAuthorityController.markExplicit()');
	});

	it('sanitizes restored direct tools and hydrates the root draft revision before model changes', () => {
		const chat = readSource('./Chat.svelte');
		const initNewChat = chat.match(
			/const initNewChat = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};\n\n\tconst loadChat/
		)?.[1];
		const rootDraftRestore = chat.match(
			/const init = async \(\) => \{([\s\S]*?)\n\t\t\};\n\t\tinit\(\);/
		)?.[1];

		expect(chat).toContain('sanitizeConversationModeSelectedToolIds');
		expect(chat).toContain('parseConversationModeDraft');
		expect(chat).toContain('getNewConversationModeDraftCapabilityAuthority');
		expect(chat).not.toContain('selectedToolIds = input.selectedToolIds;');
		expect(chat).not.toContain("Boolean(sessionStorage.getItem('chat-input'))");
		expect(chat).toContain('const restoredRootDraft = beginModeProfileDraft();');
		expect(initNewChat).toBeDefined();
		expect(initNewChat).toMatch(
			/const restoredRootCapabilityAuthority\s*=\s*getNewConversationModeDraftCapabilityAuthority/
		);
		expect(initNewChat).toContain('initialize: restoredRootCapabilityAuthority === null');
		expect(rootDraftRestore).toBeDefined();
		expect(rootDraftRestore).toContain('if (chatIdProp || restoredRootCapabilityAuthority)');
		expect(rootDraftRestore).toContain('modeProfileDraftController.hydrateRevisionHint');
		expect(
			rootDraftRestore?.indexOf('modeProfileDraftController.hydrateRevisionHint')
		).toBeGreaterThan(
			rootDraftRestore?.indexOf('modeProfileRevisionId =') ?? Number.MAX_SAFE_INTEGER
		);
	});

	it('adds permitted live direct tool server IDs to model-change availability', () => {
		const chat = readSource('./Chat.svelte');
		const availability = chat.match(
			/const getModeProfileAvailability = \(\): ConversationModeProfileAvailability => \{([\s\S]*?)\n\t\};/
		)?.[1];

		expect(chat).toContain('getConversationModeAvailableToolIds');
		expect(availability).toBeDefined();
		expect(availability).toContain('toolServers: $toolServers ?? []');
		expect(availability).toContain('directToolServersPermitted: directTerminalPermitted');
	});

	it('spreads the tri-state capability request contract instead of length-based omission', () => {
		const chat = readSource('./Chat.svelte');
		const sendMessageSocket = chat.match(/const sendMessageSocket = async \([\s\S]*?\n\t\};/)?.[0];

		expect(sendMessageSocket).toBeDefined();
		expect(sendMessageSocket).toContain('serializeConversationModeCapabilityRequest');
		expect(sendMessageSocket).toContain('serializeConversationModeToolServers');
		expect(sendMessageSocket).toContain('...capabilityRequest.request');
		expect(sendMessageSocket).toContain('...toolServersRequest');
		expect(sendMessageSocket).not.toContain('tool_servers: capabilityRequest.emitToolServers');
		expect(sendMessageSocket).not.toMatch(/selected(?:Tool|Skill|Filter)Ids\.length > 0/);
	});
});

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
		expect(navigateHandler).not.toContain('input.modeProfileRevisionId');
		expect(navigateHandler).not.toContain('hydrateRevisionHint');
		expect(chat).not.toContain('modeProfileBoundWithoutDraft');
		expect(chat).not.toContain('modeProfileCapabilitiesOverridden');
	});

	it('persists authority in both drafts and tracks only capability changes', () => {
		const chat = readSource('./Chat.svelte');
		const observations =
			chat.match(/modeProfileCapabilityAuthorityController\.observe\(data\)/g) ?? [];
		const persistedSnapshots = chat.match(/createModeProfileDraftSnapshot\(data\)/g) ?? [];

		expect(chat).toContain('createConversationModeCapabilityAuthorityController');
		expect(observations).toHaveLength(2);
		expect(persistedSnapshots).toHaveLength(2);
		expect(chat).toContain('selectedTerminalId: $selectedTerminalId ?? null');
		expect(chat).toContain('modeProfileCapabilityAuthority: modeProfileControlsReady');
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
		const beginModeProfileDraft = chat.match(
			/const beginModeProfileDraft = \([\s\S]*?\) => \{([\s\S]*?)\n\t\};/
		)?.[1];

		expect(chat).toContain('parseConversationModeDraft');
		expect(chat).toContain('getConversationModeDraftCapabilitySnapshot');
		expect(chat).not.toContain('selectedToolIds = input.selectedToolIds;');
		expect(chat).not.toContain("Boolean(sessionStorage.getItem('chat-input'))");
		expect(chat).toContain('const restoredRootDraft = beginModeProfileDraft();');
		expect(initNewChat).toBeDefined();
		expect(initNewChat).toMatch(
			/const restoredRootCapabilitySnapshot\s*=\s*getConversationModeDraftCapabilitySnapshot/
		);
		expect(initNewChat).toContain('initialize: restoredRootCapabilitySnapshot === null');
		expect(initNewChat).toContain(
			'await restoreModeProfileCapabilitySnapshot(restoredRootCapabilitySnapshot)'
		);
		expect(rootDraftRestore).toBeDefined();
		expect(rootDraftRestore).not.toContain('selectedToolIds =');
		expect(rootDraftRestore).not.toContain('modeProfileRevisionId =');
		expect(beginModeProfileDraft).toBeDefined();
		expect(beginModeProfileDraft).toContain('modeProfileDraftController.hydrateRevisionHint');
	});

	it('revalidates complete authoritative snapshots before existing chats can emit', () => {
		const chat = readSource('./Chat.svelte');
		const restoreSnapshot = chat.match(
			/const restoreModeProfileCapabilitySnapshot = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};/
		)?.[1];
		const navigateHandler = chat.match(
			/const navigateHandler = async \(\) => \{([\s\S]*?)\n\t\};\n\n\tconst onSelect/
		)?.[1];

		expect(restoreSnapshot).toBeDefined();
		expect(restoreSnapshot).toContain('selectedTerminalId.set(snapshot.selections.terminalId)');
		expect(restoreSnapshot).toContain('await revalidateModeProfileCapabilities()');
		expect(restoreSnapshot).toContain('await finalizeModeProfileCapabilitySnapshot()');
		expect(navigateHandler).toContain('getConversationModeDraftCapabilitySnapshot');
		expect(navigateHandler).toContain(
			'await restoreModeProfileCapabilitySnapshot(restoredCapabilitySnapshot)'
		);
		expect(
			navigateHandler?.indexOf(
				'await restoreModeProfileCapabilitySnapshot(restoredCapabilitySnapshot)'
			)
		).toBeLessThan(navigateHandler?.indexOf('loading = false') ?? -1);
	});

	it('adds permitted live direct tool server IDs to model-change availability', () => {
		const chat = readSource('./Chat.svelte');
		const availability = chat.match(
			/const getModeProfileAvailability = \([\s\S]*?\): ConversationModeProfileAvailability => \{([\s\S]*?)\n\t\};/
		)?.[1];

		expect(chat).toContain('getConversationModeAvailableToolIds');
		expect(availability).toBeDefined();
		expect(availability).toContain('modeProfileExternalCatalog.toolServers');
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

	it('treats an unlocked Chat/Agent selection as an awaited profile-boundary transition', () => {
		const chat = readSource('./Chat.svelte');
		const transition = chat.match(
			/const transitionConversationMode = async \(nextMode: ConversationMode\) => \{([\s\S]*?)\n\t\};/
		)?.[1];
		const setDefaults = chat.match(
			/const setDefaults = async \(\) => \{([\s\S]*?)\n\t\};\n\n\tconst showMessage/
		)?.[1];

		expect(transition).toBeDefined();
		expect(transition).toContain(
			'if (conversationModeLocked || nextMode === conversationMode) return'
		);
		expect(transition).toContain('loading = true');
		expect(transition).toContain('conversationMode = nextMode');
		expect(transition?.match(/conversationMode = nextMode/g)).toHaveLength(1);
		expect(transition).toContain('beginModeProfileDraft({ restoreRootDraft: false })');
		expect(transition).toContain('await resetInput({ initialize: true })');
		expect(transition).toContain('await finalizeModeProfileCapabilitySnapshot()');
		expect(transition).toContain('loading = false');
		expect(transition).not.toContain('selectedModels =');
		expect(transition).not.toContain('reasoningEffort =');
		expect(transition).not.toContain('files =');
		expect(transition).not.toContain('prompt =');
		expect(setDefaults).toContain('selectedModelIds.filter((id) => id).length !== 1');
		expect(chat).toContain('onConversationModeSelect={transitionConversationMode}');
		expect(chat).not.toMatch(
			/onConversationModeSelect=\{\(mode\) => \{\s*conversationMode = mode;/
		);
	});

	it('persists only finalized versioned capability snapshots after initialization or restoration', () => {
		const chat = readSource('./Chat.svelte');
		const createDraft = chat.match(
			/const createModeProfileDraftSnapshot = \(input: any\) => \(\{([\s\S]*?)\n\t\}\);/
		)?.[1];
		const finalize = chat.match(
			/const finalizeModeProfileCapabilitySnapshot = async \(\) => \{([\s\S]*?)\n\t\};/
		)?.[1];
		const persistFinalized = chat.match(
			/const persistFinalizedModeProfileDraftSnapshot = async \(\) => \{([\s\S]*?)\n\t\};/
		)?.[1];
		const initNewChat = chat.match(
			/const initNewChat = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};\n\n\tconst loadChat/
		)?.[1];

		expect(createDraft).toBeDefined();
		expect(createDraft).toContain('modeProfileControlsReady');
		expect(createDraft).toContain(
			'modeProfileCapabilitySnapshotVersion: modeProfileControlsReady ? 1 : undefined'
		);
		expect(finalize).toBeDefined();
		expect(finalize).toContain('modeProfileBoundTerminalId = $selectedTerminalId');
		expect(finalize).toContain('modeProfileControlsReady = true');
		expect(finalize).toContain('await persistFinalizedModeProfileDraftSnapshot()');
		expect(chat).toContain("modeProfileDraftId.startsWith('draft:') ? null");
		expect(persistFinalized).toBeDefined();
		expect(persistFinalized).toContain('getCurrentModeProfileDraftInput()');
		expect(persistFinalized).not.toContain('latestModeProfileDraftInput');
		expect(initNewChat).toBeDefined();
		expect(initNewChat).toContain('await finalizeModeProfileCapabilitySnapshot()');
		expect(initNewChat?.indexOf('await finalizeModeProfileCapabilitySnapshot()')).toBeGreaterThan(
			initNewChat?.indexOf('await resetInput(') ?? Number.MAX_SAFE_INTEGER
		);
		expect(chat).toContain('immediate: true');
		expect(chat).toContain('await modeProfileSetupPromise');
		expect(chat).toContain('if (settingDefaultsPromise) return settingDefaultsPromise');
	});

	it('awaits explicit external catalog truth before destructive revalidation and emission', () => {
		const chat = readSource('./Chat.svelte');
		const ensureCatalogs = chat.match(
			/const ensureModeProfileExternalCatalogsLoaded = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};/
		)?.[1];
		const revalidate = chat.match(
			/const revalidateModeProfileCapabilities = async \(\) => \{([\s\S]*?)\n\t\};/
		)?.[1];
		const sendMessageSocket = chat.match(/const sendMessageSocket = async \([\s\S]*?\n\t\};/)?.[0];

		expect(chat).toContain("import { getTerminalServers } from '$lib/apis/terminal'");
		expect(ensureCatalogs).toBeDefined();
		expect(ensureCatalogs).toContain('await getTerminalServers(localStorage.token)');
		expect(revalidate).toBeDefined();
		expect(revalidate).toContain('modeProfileControlsReady = false');
		expect(revalidate).toContain('await ensureModeProfileExternalCatalogsLoaded()');
		expect(revalidate).toContain('applyModeProfileModelChange()');
		expect(revalidate).toContain('modeProfileBoundTerminalId = $selectedTerminalId');
		expect(sendMessageSocket).toContain('await resolveModeProfileRequest(model)');
		expect(chat).toContain('configuredToolServers: $settings?.toolServers ?? []');
		expect(chat).toContain('directToolServerCatalogReady: modeProfileExternalCatalog.ready');
	});

	it('builds request-local capabilities from the actual socket model without mutating UI state', () => {
		const chat = readSource('./Chat.svelte');
		const requestResolution = chat.match(
			/const resolveModeProfileRequest = async \(model: Model\) => \{([\s\S]*?)\n\t\};/
		)?.[1];
		const sendMessageSocket = chat.match(/const sendMessageSocket = async \([\s\S]*?\n\t\};/)?.[0];

		expect(requestResolution).toBeDefined();
		expect(requestResolution).toContain('await ensureModeProfileExternalCatalogsLoaded(model)');
		expect(requestResolution).toContain('getModeProfileAvailability(model)');
		expect(requestResolution).toContain('resolveConversationModeRequestCapabilities');
		expect(requestResolution).not.toContain('applyModeProfileModelChange');
		expect(requestResolution).not.toContain('modeProfileCapabilityAuthorityController');
		expect(requestResolution).not.toContain('modeProfileRevisionId =');
		expect(requestResolution).not.toContain('modeProfileControlsReady =');
		expect(requestResolution).not.toContain('saveDraft');
		expect(requestResolution).not.toMatch(
			/(?:selectedToolIds|selectedSkillIds|selectedFilterIds|webSearchEnabled|codeInterpreterEnabled|imageGenerationEnabled)\s*=/
		);
		expect(sendMessageSocket).not.toContain('await revalidateModeProfileCapabilities()');
		expect(sendMessageSocket).toContain(
			'const modeProfileRequest = await resolveModeProfileRequest(model)'
		);
		expect(sendMessageSocket).toContain('selections: modeProfileRequest.effective');
		expect(sendMessageSocket).toContain(
			'features: getFeatures(model, modeProfileRequest.effective)'
		);
		expect(chat).toContain('const getModeProfileAvailability = (');
		expect(chat).toContain('modelOverride: Model | undefined = undefined');
	});

	it('routes every accepted submit caller through one draft-clearing boundary', () => {
		const chat = readSource('./Chat.svelte');
		const callOverlay = readSource('./MessageInput/CallOverlay.svelte');
		const submitHandler = chat.match(
			/const submitHandler = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};\n\n\tconst sendMessage/
		)?.[1];
		const suggestion = chat.match(
			/const onSelect = async \(e\) => \{([\s\S]*?)\n\t\};\n\n\t\$: if/
		)?.[1];
		const postMessage = chat.match(
			/const onMessageHandler = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};\n\n\tconst savedModelIds/
		)?.[1];
		const initNewChat = chat.match(
			/const initNewChat = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};\n\n\tconst loadChat/
		)?.[1];
		const visibleComposers = chat.match(/on:submit=\{async \(e\) => \{([\s\S]*?)\n\t+\}\}/g) ?? [];

		expect(submitHandler).toBeDefined();
		expect(submitHandler?.match(/await clearDraft\(\$chatId \|\| null\);/g) ?? []).toHaveLength(2);
		expect(chat.match(/await clearDraft\([^)]*\);/g) ?? []).toHaveLength(2);
		expect(suggestion).toContain('submitHandler(prompt)');
		expect(postMessage?.match(/submitHandler\(/g) ?? []).toHaveLength(4);
		expect(initNewChat?.match(/submitHandler\(/g) ?? []).toHaveLength(2);
		expect(chat).toContain('submitPrompt={submitHandler}');
		expect(callOverlay).toContain('await submitPrompt(res.text, { _raw: true })');
		expect(visibleComposers).toHaveLength(2);
		for (const composer of visibleComposers) {
			expect(composer).toContain('await submitHandler(e.detail)');
			expect(composer).not.toContain('clearDraft(');
		}
		expect(chat).toContain('shouldAutosaveModeProfileDraft()');
		expect(chat.match(/if \(shouldAutosaveModeProfileDraft\(\)\)/g) ?? []).toHaveLength(2);
	});

	it('clears only after validation and web-search confirmation accepts an input', () => {
		const chat = readSource('./Chat.svelte');
		const submitHandler = chat.match(
			/const submitHandler = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};\n\n\tconst sendMessage/
		)?.[1];
		const confirmWebSearch = chat.match(
			/const confirmWebSearch = async \(\) => \{([\s\S]*?)\n\t\};/
		)?.[1];

		expect(submitHandler).toBeDefined();
		const clearOffsets = [
			...(submitHandler?.matchAll(/await clearDraft\(\$chatId \|\| null\);/g) ?? [])
		].map((match) => match.index ?? -1);
		expect(clearOffsets).toHaveLength(2);
		const [queueClear, normalClear] = clearOffsets;
		for (const rejectedBeforeAcceptance of [
			'pendingOAuthTools.length > 0',
			"userPrompt === '' && files.length === 0",
			"selectedModels.includes('')",
			"file.status === 'uploading'",
			'files.length + chatFiles.length > $config?.file?.max_count',
			'openWebSearchConfirm()'
		]) {
			expect(submitHandler?.indexOf(rejectedBeforeAcceptance)).toBeLessThan(queueClear);
		}
		expect(submitHandler?.indexOf('chatRequestQueues.update')).toBeLessThan(queueClear);
		expect(queueClear).toBeLessThan(
			submitHandler?.indexOf("messageInput?.setText('')", queueClear) ?? -1
		);
		expect(submitHandler?.indexOf('currentMessage.error && !currentMessage.content')).toBeLessThan(
			normalClear
		);
		expect(normalClear).toBeLessThan(
			submitHandler?.indexOf("messageInput?.setText('')", normalClear) ?? -1
		);
		expect(normalClear).toBeLessThan(
			submitHandler?.indexOf('await submitPrompt', normalClear) ?? -1
		);
		expect(confirmWebSearch).toBeDefined();
		expect(confirmWebSearch?.indexOf('webSearchConfirmed = true')).toBeLessThan(
			confirmWebSearch?.indexOf('await submitHandler(userPrompt)') ?? -1
		);
	});
});

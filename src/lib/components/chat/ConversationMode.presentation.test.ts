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
		expect(chat).toContain('chat_mode: requestContext.mode');
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
		expect(chat).toContain('mode_profile_revision_id: requestContext.revisionHint ?? undefined');
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
			chat.match(/modeProfileCapabilityAuthorityController\.observeWithChange\(data\)/g) ?? [];
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
		expect(initNewChat).toMatch(
			/await restoreModeProfileCapabilitySnapshot\(\s*restoredRootCapabilitySnapshot,\s*expectedCatalogGeneration\s*\)/
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
		expect(restoreSnapshot).toContain('await revalidateModeProfileCapabilities({');
		expect(restoreSnapshot).toContain('expectedCatalogGeneration: expectedCatalogGeneration');
		expect(restoreSnapshot).toContain(
			'await finalizeModeProfileCapabilitySnapshot(expectedCatalogGeneration)'
		);
		expect(navigateHandler).toContain('getConversationModeDraftCapabilitySnapshot');
		expect(navigateHandler).toMatch(
			/await restoreModeProfileCapabilitySnapshot\(\s*restoredCapabilitySnapshot,\s*expectedCatalogGeneration\s*\)/
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
		expect(availability).toContain('toolServers: catalogView.toolServers');
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
			/const setDefaults = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};\n\n\tconst showMessage/
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
		expect(transition).toContain(
			'await finalizeModeProfileCapabilitySnapshot(expectedCatalogGeneration)'
		);
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
			/const finalizeModeProfileCapabilitySnapshot = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};/
		)?.[1];
		const persistFinalized = chat.match(
			/const persistFinalizedModeProfileDraftSnapshot = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};/
		)?.[1];
		const initNewChat = chat.match(
			/const initNewChat = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};\n\n\tconst loadChat/
		)?.[1];

		expect(createDraft).toBeDefined();
		expect(createDraft).toContain('modeProfileControlsReady');
		expect(createDraft).toContain(
			'modeProfileCapabilitySnapshotVersion: modeProfileControlsReady ? 1 : undefined'
		);
		expect(createDraft).toContain('modeProfileCapabilityOverrideFields');
		expect(finalize).toBeDefined();
		expect(finalize).toContain('modeProfileBoundTerminalId = $selectedTerminalId');
		expect(finalize).toContain('modeProfileControlsReady = true');
		expect(finalize).toContain(
			'await persistFinalizedModeProfileDraftSnapshot(expectedCatalogGeneration)'
		);
		expect(chat).toContain("modeProfileDraftId.startsWith('draft:') ? null");
		expect(persistFinalized).toBeDefined();
		expect(persistFinalized).toContain('getCurrentModeProfileDraftInput()');
		expect(persistFinalized).not.toContain('latestModeProfileDraftInput');
		expect(initNewChat).toBeDefined();
		const finalizeCall = 'await finalizeModeProfileCapabilitySnapshot(expectedCatalogGeneration)';
		expect(initNewChat).toContain(finalizeCall);
		expect(initNewChat?.indexOf(finalizeCall)).toBeGreaterThan(
			initNewChat?.indexOf('await resetInput(') ?? Number.MAX_SAFE_INTEGER
		);
		expect(chat).toContain('immediate: true');
		expect(chat).toContain('await modeProfileSetupPromise');
		expect(chat).toContain('const inFlight = settingDefaultsPromise');
		expect(chat).toContain('inFlight.generation === expectedCatalogGeneration');
	});

	it('caches bounded external discovery while send uses a non-blocking catalog snapshot', () => {
		const chat = readSource('./Chat.svelte');
		const refreshCatalogs = chat.match(
			/const refreshModeProfileExternalCatalogs = \([\s\S]*?\) => \{([\s\S]*?)\n\t\};/
		)?.[1];
		const revalidate = chat.match(
			/const revalidateModeProfileCapabilities = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};/
		)?.[1];
		const triggerRefresh = chat.match(
			/const triggerModeProfileExternalCatalogRefresh = \([\s\S]*?\) => \{([\s\S]*?)\n\t\};/
		)?.[1];
		const requestResolution = chat.match(
			/const resolveModeProfileRequest = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};/
		)?.[1];

		expect(chat).toContain("import { getTerminalServers } from '$lib/apis/terminal'");
		expect(chat).toContain('MODE_PROFILE_EXTERNAL_CATALOG_TIMEOUT_MS');
		expect(chat).toContain('withModeProfileExternalCatalogTimeout');
		expect(chat).toContain('createConversationModeExternalCatalogCache');
		expect(chat).toContain('getConversationModeExternalCatalogFingerprint');
		expect(chat).toContain('MODE_PROFILE_EXTERNAL_CATALOG_MAX_AGE_MS');
		expect(chat).toContain('modeProfileExternalCatalogCache.shouldRefresh');
		expect(refreshCatalogs).toBeDefined();
		expect(refreshCatalogs).toContain('modeProfileExternalCatalogPromises.get(fingerprint)');
		expect(refreshCatalogs).toContain('modeProfileExternalCatalogCache.begin');
		expect(refreshCatalogs).toMatch(
			/withModeProfileExternalCatalogTimeout\([\s\S]*getTerminalServers\(localStorage\.token, \{[\s\S]*throwOnError: true/
		);
		expect(refreshCatalogs?.match(/withModeProfileExternalCatalogTimeout\(/g) ?? []).toHaveLength(
			3
		);
		expect(revalidate).toBeDefined();
		expect(revalidate).toContain('modeProfileControlsReady = false');
		expect(revalidate).toContain('await refreshModeProfileExternalCatalogs(');
		expect(revalidate).toContain('await applyModeProfileModelChange(');
		expect(revalidate).toContain('modeProfileBoundTerminalId = $selectedTerminalId');
		expect(triggerRefresh).toBeDefined();
		expect(triggerRefresh).toContain('void refreshModeProfileExternalCatalogs(');
		expect(triggerRefresh).not.toContain('await refreshModeProfileExternalCatalogs(');
		expect(triggerRefresh).toContain('shouldRefreshModeProfileExternalCatalog');
		expect(requestResolution).toBeDefined();
		expect(requestResolution).toContain('triggerModeProfileExternalCatalogRefresh(');
		expect(requestResolution).not.toContain('await refreshModeProfileExternalCatalogs(');
		expect(requestResolution).toContain('getModeProfileExternalCatalogView(');
	});

	it('invalidates background catalog callbacks across conversation and component lifecycles', () => {
		const chat = readSource('./Chat.svelte');
		const triggerRefresh = chat.match(
			/const triggerModeProfileExternalCatalogRefresh = \([\s\S]*?\) => \{([\s\S]*?)\n\t\};/
		)?.[1];
		const revalidate = chat.match(
			/const revalidateModeProfileCapabilities = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};/
		)?.[1];
		const navigateHandler = chat.match(
			/const navigateHandler = async \(\) => \{([\s\S]*?)\n\t\};\n\n\tconst onSelect/
		)?.[1];
		const beginModeProfileDraft = chat.match(
			/const beginModeProfileDraft = \([\s\S]*?\) => \{([\s\S]*?)\n\t\};/
		)?.[1];
		const continueOAuthRedirect = chat.match(
			/const continueOAuthRedirect = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};/
		)?.[1];
		const applyModelChange = chat.match(
			/const applyModeProfileModelChange = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};/
		)?.[1];
		const setDefaults = chat.match(
			/const setDefaults = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};/
		)?.[1];
		const initNewChat = chat.match(
			/const initNewChat = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};\n\n\tconst loadChat/
		)?.[1];
		const restoreSnapshot = chat.match(
			/const restoreModeProfileCapabilitySnapshot = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};/
		)?.[1];
		const restoreLegacy = chat.match(
			/const restoreLegacyModeProfileDraftCapabilities = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};/
		)?.[1];
		const finalize = chat.match(
			/const finalizeModeProfileCapabilitySnapshot = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};/
		)?.[1];
		const persistFinalized = chat.match(
			/const persistFinalizedModeProfileDraftSnapshot = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};/
		)?.[1];
		const transition = chat.match(
			/const transitionConversationMode = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};/
		)?.[1];

		expect(chat).toContain('let modeProfileCatalogGeneration = 0');
		expect(triggerRefresh).toContain('const refreshGeneration = modeProfileCatalogGeneration');
		expect(triggerRefresh).toContain('refreshGeneration !== modeProfileCatalogGeneration');
		expect(triggerRefresh).toContain('expectedCatalogGeneration: refreshGeneration');
		expect(revalidate).toContain('expectedCatalogGeneration');
		expect(
			revalidate?.match(/isModeProfileCatalogGenerationCurrent\(/g)?.length ?? 0
		).toBeGreaterThanOrEqual(4);
		expect(navigateHandler).toContain('invalidateModeProfileCatalogGeneration()');
		expect(beginModeProfileDraft).toContain('invalidateModeProfileCatalogGeneration()');
		expect(applyModelChange).toContain(
			"applyModeProfileResolution(snapshot, 'model_change', expectedCatalogGeneration)"
		);
		expect(continueOAuthRedirect).toContain('expectedCatalogGeneration');
		expect(continueOAuthRedirect).toMatch(
			/await tick\(\);[\s\S]*?isModeProfileCatalogGenerationCurrent\(expectedCatalogGeneration\)[\s\S]*?initiateOAuthRedirect/
		);
		expect(setDefaults).toContain('expectedCatalogGeneration');
		expect(
			setDefaults?.match(/isModeProfileCatalogGenerationCurrent\(/g)?.length ?? 0
		).toBeGreaterThanOrEqual(3);
		expect(setDefaults).toContain('continueOAuthRedirect(expectedCatalogGeneration)');
		expect(setDefaults).toContain(
			'applyModeProfileInitialization(externalCatalogRequest, expectedCatalogGeneration)'
		);
		expect(chat).toContain('filterConversationModeTerminalCandidateIds');
		expect(chat).toContain('configuredDirectTerminalIds');
		expect(initNewChat).toContain('const expectedCatalogGeneration = modeProfileCatalogGeneration');
		expect(initNewChat).toMatch(
			/await resetInput\([\s\S]*?isModeProfileCatalogGenerationCurrent\(expectedCatalogGeneration\)[\s\S]*?restoreModeProfileCapabilitySnapshot/
		);
		expect(initNewChat).toMatch(
			/restoreLegacyModeProfileDraftCapabilities\([\s\S]*?isModeProfileCatalogGenerationCurrent\(expectedCatalogGeneration\)[\s\S]*?await chatId\.set\(''\)/
		);
		expect(initNewChat).toMatch(
			/restoreModeProfileCapabilitySnapshot\(\s*restoredRootCapabilitySnapshot,\s*expectedCatalogGeneration\s*\)/
		);
		expect(initNewChat).toMatch(
			/restoreLegacyModeProfileDraftCapabilities\(\s*restoredRootDraft,\s*\{[\s\S]*?expectedCatalogGeneration/
		);
		expect(initNewChat).toContain(
			'finalizeModeProfileCapabilitySnapshot(expectedCatalogGeneration)'
		);
		expect(restoreSnapshot).toContain('expectedCatalogGeneration');
		expect(restoreSnapshot).toContain('expectedCatalogGeneration: expectedCatalogGeneration');
		expect(restoreSnapshot).toContain(
			'finalizeModeProfileCapabilitySnapshot(expectedCatalogGeneration)'
		);
		expect(restoreLegacy).toContain('expectedCatalogGeneration');
		expect(finalize).toContain('expectedCatalogGeneration');
		expect(finalize).toContain(
			'persistFinalizedModeProfileDraftSnapshot(expectedCatalogGeneration)'
		);
		expect(persistFinalized).toMatch(
			/await tick\(\);[\s\S]*?isModeProfileCatalogGenerationCurrent\(expectedCatalogGeneration\)[\s\S]*?saveDraft/
		);
		expect(transition).toContain('let expectedCatalogGeneration = modeProfileCatalogGeneration');
		expect(transition).toContain(
			'finalizeModeProfileCapabilitySnapshot(expectedCatalogGeneration)'
		);
		expect(chat).toMatch(
			/return \(\) => \{[\s\S]*?invalidateModeProfileCatalogGeneration\(\)[\s\S]*?pageSubscribe\(\)/
		);
	});

	it('captures request state before the first await and serializes only from that context', () => {
		const chat = readSource('./Chat.svelte');
		const requestResolution = chat.match(
			/const resolveModeProfileRequest = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};/
		)?.[1];
		const sendMessageSocket = chat.match(/const sendMessageSocket = async \([\s\S]*?\n\t\};/)?.[0];

		expect(requestResolution).toBeDefined();
		expect(requestResolution).toMatch(/getModeProfileAvailability\(\s*requestContext\.model/);
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
		expect(sendMessageSocket).not.toContain('await modeProfileSetupPromise');
		expect(sendMessageSocket).toContain('captureConversationModeRequestContext');
		const captureOffset = sendMessageSocket?.indexOf(
			'const requestContext = captureConversationModeRequestContext'
		);
		const firstAwaitOffset = sendMessageSocket?.indexOf('await ');
		expect(captureOffset).toBeGreaterThanOrEqual(0);
		expect(captureOffset).toBeLessThan(firstAwaitOffset ?? -1);
		expect(sendMessageSocket).toMatch(
			/const modeProfileRequest = await resolveModeProfileRequest\(\s*requestContext/
		);
		expect(sendMessageSocket).toContain('selections: modeProfileRequest.effective');
		expect(sendMessageSocket).toContain('getConversationModeRequestFeatures');
		expect(sendMessageSocket).toContain('authority: requestContext.authority');
		expect(sendMessageSocket).toContain('overrideFields: requestContext.overrideFields');
		expect(sendMessageSocket).toContain('chat_mode: requestContext.mode');
		expect(sendMessageSocket).toContain(
			'mode_profile_revision_id: requestContext.revisionHint ?? undefined'
		);
		expect(sendMessageSocket).toContain('model_item: requestContext.model');
		expect(sendMessageSocket).toContain('requestContext.model.info?.meta?.capabilities?.usage');
		expect(sendMessageSocket).not.toContain('stream && (model.info?.meta?.capabilities?.usage');
		expect(chat).toContain('const getModeProfileAvailability = (');
		expect(chat).toContain('requestContext?: ConversationModeRequestContext');
	});

	it('wires positive legacy migration for root and existing drafts without replacing ordinary content', () => {
		const chat = readSource('./Chat.svelte');
		const navigateHandler = chat.match(
			/const navigateHandler = async \(\) => \{([\s\S]*?)\n\t\};\n\n\tconst onSelect/
		)?.[1];
		const initNewChat = chat.match(
			/const initNewChat = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};\n\n\tconst loadChat/
		)?.[1];
		const restoreLegacy = chat.match(
			/const restoreLegacyModeProfileDraftCapabilities = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};/
		)?.[1];

		expect(chat).toContain('migrateConversationModeLegacyDraftCapabilities');
		expect(restoreLegacy).toBeDefined();
		expect(restoreLegacy).toContain("phase: 'initialize'");
		expect(restoreLegacy).toContain("phase: 'model_change'");
		expect(restoreLegacy).toContain('modeProfileCapabilityAuthorityController.markExplicit()');
		expect(navigateHandler).toMatch(
			/restoreLegacyModeProfileDraftCapabilities\(\s*restoredDraft,\s*\{[\s\S]*?preserveBoundDefaults: true,[\s\S]*?expectedCatalogGeneration[\s\S]*?\}[\s\S]*?\)/
		);
		expect(initNewChat).toMatch(
			/await restoreLegacyModeProfileDraftCapabilities\(\s*restoredRootDraft,\s*\{[\s\S]*?expectedCatalogGeneration[\s\S]*?\}\s*\)/
		);
		expect(navigateHandler).toContain('messageInput?.setText(restoredDraft.prompt)');
		expect(navigateHandler).toContain('files = restoredDraft.files');
	});

	it('binds controls autosave responses only to their originating chat generation', () => {
		const chat = readSource('./Chat.svelte');
		const saveControls = chat.match(/const saveControls = async \(\) => \{([\s\S]*?)\n\t\};/)?.[1];

		expect(saveControls).toBeDefined();
		expect(saveControls).toContain('const targetChatId = $chatId');
		expect(saveControls).toContain(
			'const expectedCatalogGeneration = modeProfileCatalogGeneration'
		);
		expect(saveControls).toContain('updateChatById(localStorage.token, targetChatId');
		expect(saveControls).toMatch(
			/if \([\s\S]*?\$chatId !== targetChatId[\s\S]*?!isModeProfileCatalogGenerationCurrent\(expectedCatalogGeneration\)[\s\S]*?\)\s*return;/
		);
		expect(saveControls?.indexOf('$chatId !== targetChatId')).toBeLessThan(
			saveControls?.indexOf('chat = res') ?? -1
		);
		expect(saveControls?.indexOf('$chatId !== targetChatId')).toBeLessThan(
			saveControls?.indexOf('bindCanonicalModeProfileRevision') ?? -1
		);
	});

	it('commits loaded chat responses only to their originating navigation generation', () => {
		const chat = readSource('./Chat.svelte');
		const loadChat = chat.match(
			/const loadChat = async \([\s\S]*?\) => \{[\s\S]*?\n\t\};\n\n\tconst scrollToBottom/
		)?.[0];
		const navigateHandler = chat.match(
			/const navigateHandler = async \(\) => \{([\s\S]*?)\n\t\};\n\n\tconst onSelect/
		)?.[1];

		expect(loadChat).toBeDefined();
		expect(loadChat).toContain('targetChatId = chatIdProp');
		expect(loadChat).toContain('expectedCatalogGeneration = modeProfileCatalogGeneration');
		expect(loadChat).toContain(
			'const loadedChat = await getChatById(localStorage.token, targetChatId)'
		);
		expect(loadChat).toContain(
			'const loadedTags = await getTagsById(localStorage.token, targetChatId)'
		);
		expect(loadChat).not.toContain('chat = await getChatById');
		expect(loadChat).toMatch(
			/\$chatId !== targetChatId[\s\S]*?!isModeProfileCatalogGenerationCurrent\(expectedCatalogGeneration\)[\s\S]*?return false;[\s\S]*?chat = loadedChat/
		);
		expect(loadChat?.indexOf('chat = loadedChat')).toBeLessThan(
			loadChat?.indexOf('bindCanonicalModeProfileRevision') ?? -1
		);
		expect(navigateHandler).toContain('loadChat(chatIdProp, expectedCatalogGeneration)');
	});

	it('guards deferred authority, tag refresh, and chat actions by chat generation', () => {
		const chat = readSource('./Chat.svelte');
		const navigateHandler = chat.match(
			/const navigateHandler = async \(\) => \{([\s\S]*?)\n\t\};\n\n\tconst onSelect/
		)?.[1];
		const chatEventHandler = chat.match(
			/const chatEventHandler = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};\n\n\tconst onMessageHandler/
		)?.[1];
		const chatActionHandler = chat.match(
			/const chatActionHandler = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};\n\n\tconst getChatEventEmitter/
		)?.[1];

		expect(navigateHandler).toMatch(
			/queueMicrotask\(\(\) => \{\s*if \(!isModeProfileCatalogGenerationCurrent\(expectedCatalogGeneration\)\) return;/
		);
		expect(chatEventHandler).toContain('const loadedTaggedChat = await getChatById(');
		expect(chatEventHandler).toContain('const loadedAllTags = await getAllTags(');
		expect(chatEventHandler).toMatch(
			/loadedAllTags[\s\S]*?event\.chat_id !== \$chatId[\s\S]*?!isModeProfileCatalogGenerationCurrent\(expectedCatalogGeneration\)[\s\S]*?return;[\s\S]*?chat = loadedTaggedChat[\s\S]*?allTags\.set\(loadedAllTags\)/
		);
		expect(chatActionHandler).toContain(
			'const expectedCatalogGeneration = modeProfileCatalogGeneration'
		);
		expect(chatActionHandler).toContain('const targetChatId = _chatId');
		expect(chatActionHandler).toMatch(
			/const res = await chatAction[\s\S]*?if \([\s\S]*?\$chatId !== targetChatId[\s\S]*?!isModeProfileCatalogGenerationCurrent\(expectedCatalogGeneration\)[\s\S]*?\)\s*\{\s*return;\s*\}[\s\S]*?history\.messages/
		);
		expect(chatActionHandler).not.toContain('chat = await updateChatById');
		expect(chatActionHandler).toContain('const savedChat = await updateChatById');
	});

	it('keeps socket completion and queued requests owned by their originating chat generation', () => {
		const chat = readSource('./Chat.svelte');
		const chatEventHandler = chat.match(
			/const chatEventHandler = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};\n\n\tconst onMessageHandler/
		)?.[1];
		const processNextInQueue = chat.match(
			/const processNextInQueue = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};\n\n\tconst chatCompletedHandler/
		)?.[1];
		const chatCompletedHandler = chat.match(
			/const chatCompletedHandler = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};\n\n\tconst chatActionHandler/
		)?.[1];
		const chatCompletionEventHandler = chat.match(
			/const chatCompletionEventHandler = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};\n\n\t\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\//
		)?.[1];
		const stopResponse = chat.match(
			/const stopResponse = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};\n\n\tconst submitMessage/
		)?.[1];

		expect(chatEventHandler).toMatch(
			/chatCompletionEventHandler\(\s*data,\s*message,\s*event\.chat_id,\s*expectedCatalogGeneration\s*\)/
		);
		expect(chatEventHandler).toContain(
			'processNextInQueue(event.chat_id, expectedCatalogGeneration)'
		);
		expect(chat).toMatch(
			/const processNextInQueue = async \(\s*targetChatId: string,\s*expectedCatalogGeneration = modeProfileCatalogGeneration\s*\)/
		);
		expect(processNextInQueue).toMatch(
			/\$chatId !== targetChatId[\s\S]*?!isModeProfileCatalogGenerationCurrent\(expectedCatalogGeneration\)[\s\S]*?return;[\s\S]*?chatRequestQueues\.update[\s\S]*?await submitPrompt/
		);
		expect(chat).toMatch(
			/const chatCompletionEventHandler = async \(\s*data,\s*message,\s*targetChatId,\s*expectedCatalogGeneration = modeProfileCatalogGeneration\s*\)/
		);
		expect(chatCompletionEventHandler).toContain(
			'processNextInQueue(targetChatId, expectedCatalogGeneration)'
		);
		expect(chatCompletionEventHandler).toMatch(
			/chatCompletedHandler\(\s*targetChatId,\s*message\.model,\s*message\.id,\s*expectedCatalogGeneration\s*\)/
		);
		expect(chatCompletedHandler).toContain('const loadedChats = await getChatList');
		expect(chatCompletedHandler).toMatch(
			/const loadedChats = await getChatList[\s\S]*?\$chatId !== targetChatId[\s\S]*?!isModeProfileCatalogGenerationCurrent\(expectedCatalogGeneration\)[\s\S]*?return;[\s\S]*?chats\.set\(loadedChats\)/
		);
		expect(stopResponse).toContain('const targetChatId = $chatId');
		expect(stopResponse).toContain('processNextInQueue(targetChatId, expectedCatalogGeneration)');
	});

	it('partitions OAuth tools only when applying global UI resolutions', () => {
		const chat = readSource('./Chat.svelte');
		const applyResolution = chat.match(
			/const applyModeProfileResolution = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};/
		)?.[1];
		const requestResolution = chat.match(
			/const resolveModeProfileRequest = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};/
		)?.[1];

		expect(chat).toContain('partitionConversationModeOAuthTools');
		expect(applyResolution).toBeDefined();
		expect(applyResolution).toContain('partitionConversationModeOAuthTools');
		expect(applyResolution).toContain('pendingOAuthTools = oauthPartition.pendingOAuthTools');
		expect(applyResolution).toContain('await continueOAuthRedirect(expectedCatalogGeneration)');
		expect(requestResolution).toBeDefined();
		expect(requestResolution).not.toContain('pendingOAuthTools');
		expect(chat).toContain('getModeProfileSelectedToolIds');
		expect(chat).toContain('pendingOAuthTools.map((tool) => tool.id)');
		expect(chat).toContain('includePendingOAuthTools: false');
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
		expect(
			submitHandler?.match(/await runAcceptedSubmitDraftCriticalSection\(/g) ?? []
		).toHaveLength(2);
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
		const criticalSectionOffsets = [
			...(submitHandler?.matchAll(/await runAcceptedSubmitDraftCriticalSection\(/g) ?? [])
		].map((match) => match.index ?? -1);
		expect(criticalSectionOffsets).toHaveLength(2);
		const [queueCriticalSection, normalCriticalSection] = criticalSectionOffsets;
		for (const rejectedBeforeAcceptance of [
			'pendingOAuthTools.length > 0',
			"userPrompt === '' && files.length === 0",
			"selectedModels.includes('')",
			"file.status === 'uploading'",
			'files.length + chatFiles.length > $config?.file?.max_count',
			'openWebSearchConfirm()'
		]) {
			expect(submitHandler?.indexOf(rejectedBeforeAcceptance)).toBeLessThan(queueCriticalSection);
		}
		expect(submitHandler?.indexOf('chatRequestQueues.update')).toBeLessThan(queueCriticalSection);
		expect(queueCriticalSection).toBeLessThan(
			submitHandler?.indexOf("messageInput?.setText('')", queueCriticalSection) ?? -1
		);
		expect(submitHandler?.indexOf('currentMessage.error && !currentMessage.content')).toBeLessThan(
			normalCriticalSection
		);
		expect(normalCriticalSection).toBeLessThan(
			submitHandler?.indexOf("messageInput?.setText('')", normalCriticalSection) ?? -1
		);
		expect(normalCriticalSection).toBeLessThan(
			submitHandler?.indexOf('await submitPrompt', normalCriticalSection) ?? -1
		);
		expect(confirmWebSearch).toBeDefined();
		expect(confirmWebSearch?.indexOf('webSearchConfirmed = true')).toBeLessThan(
			confirmWebSearch?.indexOf('await submitHandler(userPrompt)') ?? -1
		);
	});

	it('clears the accepted create-message-pair shortcut after model validation and before history mutation', () => {
		const chat = readSource('./Chat.svelte');
		const input = readSource('./MessageInput.svelte');
		const createMessagePair = chat.match(
			/const createMessagePair = async \(userPrompt\) => \{([\s\S]*?)\n\t\};\n\n\tconst addMessages/
		)?.[1];

		expect(createMessagePair).toBeDefined();
		expect(input).toContain('on:click={() => createMessagePair(prompt)}');
		expect(chat.match(/\{createMessagePair\}/g) ?? []).toHaveLength(2);
		expect(
			createMessagePair?.match(/await runAcceptedSubmitDraftCriticalSection\(/g) ?? []
		).toHaveLength(1);
		const criticalSectionOffset =
			createMessagePair?.indexOf('await runAcceptedSubmitDraftCriticalSection(') ?? -1;
		expect(createMessagePair?.indexOf('selectedModels.length === 0')).toBeLessThan(
			criticalSectionOffset
		);
		expect(createMessagePair?.indexOf("toast.error($i18n.t('Model not selected'))")).toBeLessThan(
			criticalSectionOffset
		);
		expect(createMessagePair?.indexOf('if (!model)')).toBeLessThan(criticalSectionOffset);
		expect(createMessagePair?.indexOf("toast.error($i18n.t('Model not found'))")).toBeLessThan(
			criticalSectionOffset
		);
		expect(criticalSectionOffset).toBeLessThan(
			createMessagePair?.indexOf('createMessagesList') ?? -1
		);
		expect(criticalSectionOffset).toBeLessThan(
			createMessagePair?.indexOf('history.messages[userMessageId]') ?? -1
		);
		expect(criticalSectionOffset).toBeLessThan(
			createMessagePair?.indexOf('await initChatHandler') ?? -1
		);
	});

	it('suppresses every reactive draft write until accepted input reset onChange has flushed', () => {
		const chat = readSource('./Chat.svelte');
		const criticalSection = chat.match(
			/const runAcceptedSubmitDraftCriticalSection = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};/
		)?.[1];
		const saveDraft = chat.match(
			/const saveDraft = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};/
		)?.[1];
		const persistFinalized = chat.match(
			/const persistFinalizedModeProfileDraftSnapshot = async \([\s\S]*?\) => \{([\s\S]*?)\n\t\};/
		)?.[1];
		const saveAuthority = chat.match(
			/const saveModeProfileCapabilityAuthority = \(\) => \{([\s\S]*?)\n\t\};/
		)?.[1];

		expect(criticalSection).toBeDefined();
		const suppressOffset =
			criticalSection?.indexOf('acceptedSubmitDraftPersistenceSuppressed = true') ?? -1;
		const clearOffset = criticalSection?.indexOf('await clearDraft($chatId || null)') ?? -1;
		const resetOffset = criticalSection?.indexOf('await clearInput()') ?? -1;
		const tickOffset = criticalSection?.indexOf('await tick()') ?? -1;
		const releaseOffset =
			criticalSection?.indexOf('acceptedSubmitDraftPersistenceSuppressed = false') ?? -1;
		expect(suppressOffset).toBeLessThan(clearOffset);
		expect(clearOffset).toBeLessThan(resetOffset);
		expect(resetOffset).toBeLessThan(tickOffset);
		expect(tickOffset).toBeLessThan(releaseOffset);
		expect(criticalSection).toContain('finally');
		expect(criticalSection).not.toContain('submitPrompt');
		expect(criticalSection).not.toContain('createMessagesList');
		expect(saveDraft).toContain('if (acceptedSubmitDraftPersistenceSuppressed) return');
		expect(persistFinalized).toContain('acceptedSubmitDraftPersistenceSuppressed');
		expect(saveAuthority).toContain('acceptedSubmitDraftPersistenceSuppressed');
		expect(
			chat.match(
				/if \(\s*!acceptedSubmitDraftPersistenceSuppressed\s*&&\s*shouldAutosaveModeProfileDraft\(\)\s*\)/g
			) ?? []
		).toHaveLength(2);
	});
});

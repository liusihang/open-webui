<script lang="ts">
	import { v4 as uuidv4 } from 'uuid';
	import { toast } from 'svelte-sonner';
	import { PaneGroup, Pane, PaneResizer } from 'paneforge';

	import { getContext, onDestroy, onMount, tick } from 'svelte';
	import { fade } from 'svelte/transition';
	const i18n: Writable<i18nType> = getContext('i18n');

	import { goto } from '$app/navigation';
	import { page } from '$app/stores';

	import { get, type Unsubscriber, type Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { WEBUI_BASE_URL } from '$lib/constants';
	import equal from 'fast-deep-equal';

	import {
		chatId,
		config,
		type Model,
		models,
		tags as allTags,
		settings,
		showSidebar,
		WEBUI_NAME,
		banners,
		user,
		socket,
		audioQueue,
		showControls,
		showCallOverlay,
		temporaryChatEnabled,
		mobile,
		chatTitle,
		showArtifacts,
		artifactContents,
		tools,
		skills,
		toolServers,
		terminalServers,
		functions,
		selectedFolder,
		showEmbeds,
		selectedTerminalId,
		showFileNavPath,
		showFileNavDir,
		chatRequestQueues,
		desktopEvent
	} from '$lib/stores';
	import { refreshChatList, refreshFolderChatLists } from '$lib/stores/chatList';

	import { WEBUI_API_BASE_URL } from '$lib/constants';

	import {
		convertMessagesToHistory,
		copyToClipboard,
		getMessageContentParts,
		createMessagesList,
		sanitizeHistory,
		getPromptVariables,
		processDetails,
		removeAllDetails,
		getCodeBlockContents,
		isYoutubeUrl,
		displayFileHandler
	} from '$lib/utils';
	import { AudioQueue } from '$lib/utils/audio';
	import { createTemporaryChatId, isTemporaryChatId } from '$lib/utils/chatId';
	import { getOutputText } from './Messages/structuredOutput';

	import {
		archiveChatById,
		compactChatById,
		createNewChat,
		deleteChatById,
		forkChatById,
		getAllTags,
		getChatById,
		getTagsById,
		updateChatById,
		updateChatFolderIdById
	} from '$lib/apis/chats';
	import { generateOpenAIChatCompletion } from '$lib/apis/openai';
	import { processWeb, processWebSearch, processYoutubeVideo } from '$lib/apis/retrieval';
	import { getAndUpdateUserLocation, getUserSettings } from '$lib/apis/users';
	import {
		generateQueries,
		chatAction,
		generateMoACompletion,
		stopTask,
		stopTasksByChatId,
		getTaskIdsByChatId,
		getToolServersData
	} from '$lib/apis';
	import { getTerminalServers } from '$lib/apis/terminal';
	import { getTools } from '$lib/apis/tools';
	import { getSkills } from '$lib/apis/skills';
	import { uploadFile } from '$lib/apis/files';
	import { createOpenAITextStream } from '$lib/apis/streaming';
	import { getFunctions } from '$lib/apis/functions';
	import { initiateOAuthRedirect } from '$lib/apis/configs';
	import { updateFolderById } from '$lib/apis/folders';

	import Banner from '../common/Banner.svelte';
	import MessageInput from '$lib/components/chat/MessageInput.svelte';
	import { shouldEnableImageGenerationByDefault } from '$lib/components/chat/defaultFeatures';
	import {
		buildModelReasoningPayload,
		isAgentModeCapabilityEnabled,
		normalizeConversationMode,
		normalizeReasoningEffort,
		resolveConversationModeRequestModels,
		type ConversationMode,
		type ReasoningEffort
	} from '$lib/components/chat/agentModeRequest';
	import {
		captureConversationModeRequestContext,
		createConversationModeCapabilityAuthorityController,
		createConversationModeExternalCatalogCache,
		createConversationModeProfileDraftController,
		filterConversationModeTerminalCandidateIds,
		getConversationModeAvailableToolIds,
		getConversationModeDraftCapabilitySnapshotForMode,
		getConversationModeExternalCatalogFingerprint,
		getConversationModeRequestFeatures,
		isConversationModeDraftCompatible,
		isDirectToolServersPermitted,
		migrateConversationModeLegacyDraftCapabilities,
		parseConversationModeDraft,
		partitionConversationModeOAuthTools,
		resolveConversationModeProfile,
		resolveConversationModeRequestCapabilities,
		serializeConversationModeCapabilityRequest,
		serializeConversationModeToolServers,
		type ConversationModeCapabilityAuthority,
		type ConversationModeCapabilityOverrideField,
		type ConversationModeDraftCapabilitySnapshot,
		type ConversationModePendingOAuthTool,
		type ConversationModeProfileAvailability,
		type ConversationModeProfileSelections,
		type ConversationModeRequestContext,
		type ConversationModeToolServer
	} from '$lib/components/chat/conversationModeProfiles';
	import type { ConversationModeProfilePublic } from '$lib/apis/configs';
	import {
		prepareLoadedChatHistory,
		shouldApplySocketContentEvent
	} from '$lib/components/chat/historySync';
	import Messages from '$lib/components/chat/Messages.svelte';
	import Navbar from '$lib/components/chat/Navbar.svelte';
	import ChatControls from './ChatControls.svelte';
	import EventConfirmDialog from '../common/ConfirmDialog.svelte';
	import ConversationModeConfirmDialog from '../common/ConfirmDialog.svelte';
	import DeleteConfirmDialog from '../common/ConfirmDialog.svelte';
	import WebSearchConfirmDialog from '../common/ConfirmDialog.svelte';
	import Placeholder from './Placeholder.svelte';
	import FilesOverlay from './MessageInput/FilesOverlay.svelte';
	import NotificationToast from '../NotificationToast.svelte';
	import Spinner from '../common/Spinner.svelte';
	import Modal from '../common/Modal.svelte';
	import { isEmbedWindow } from '../common/FullHeightIframe.svelte';
	import Tooltip from '../common/Tooltip.svelte';
	import Sidebar from '../icons/Sidebar.svelte';
	import Image from '../common/Image.svelte';
	import XMark from '../icons/XMark.svelte';
	import EmbeddedChatHistoryDropdown from './EmbeddedChatHistoryDropdown.svelte';
	import InputVariablesModal from './MessageInput/InputVariablesModal.svelte';

	export let chatIdProp = '';
	export let embedded = false;
	export let embeddedTitle = '';
	export let embeddedChats = [];
	export let embeddedDraftKey = '';
	export let suggestedPrompts = [];
	export let selectedText = '';
	export let onInsertToNote: ((content: string) => void) | null = null;
	export let onCloseEmbedded: (() => void) | null = null;
	export let onNewEmbeddedChat: (() => void | Promise<void>) | null = null;
	export let onCreateEmbeddedChat: (() => any | Promise<any>) | null = null;
	export let onSelectEmbeddedChat: ((chatId: string) => void | Promise<void>) | null = null;
	export let onDeleteEmbeddedChat: ((chatId: string) => void | Promise<void>) | null = null;
	export let onEmbeddedChatTitle: ((chatId: string, title: string) => void | Promise<void>) | null =
		null;

	let loading = true;
	$: chatContainerId = embedded ? 'note-chat-container' : 'chat-container';
	$: messageInputDropzoneId = embedded ? 'note-chat-input-dropzone' : 'chat-pane';

	const eventTarget = new EventTarget();
	let controlPane: Pane | undefined;
	let controlPaneComponent: ChatControls | undefined;

	let messageInput: MessageInput | undefined;
	let messagesRef: Messages | undefined;

	let autoScroll = true;
	let isNearTop = true;
	let processing = '';
	let messagesContainerElement: HTMLDivElement;

	let navbarElement;

	let showEventConfirmation = false;
	let showConversationModeConfirmation = false;
	let pendingConversationMode: ConversationMode | null = null;
	let eventConfirmationTitle = '';
	let eventConfirmationMessage = '';
	let eventConfirmationInput = false;
	let eventConfirmationInputPlaceholder = '';
	let eventConfirmationInputValue = '';
	let eventConfirmationInputType = '';
	let eventConfirmationInputOptions: ({ label?: string; value: string } | string)[] = [];
	let eventCallback = null;

	let selectedModels = [''];
	let conversationMode: ConversationMode = 'chat';
	$: agentModeAvailable = isAgentModeCapabilityEnabled($config);
	$: agentConversationUnavailable = conversationMode === 'agent' && !agentModeAvailable;
	let atSelectedModel: Model | undefined;
	let selectedModelIds: string[] = [];
	$: if (atSelectedModel !== undefined) {
		selectedModelIds = resolveConversationModeRequestModels([atSelectedModel.id], conversationMode);
	} else {
		selectedModelIds = resolveConversationModeRequestModels(selectedModels, conversationMode);
	}
	let serverContextUsage = null;
	let contextUsage = null;

	const getAvailableModelIds = () =>
		$models.filter((m) => !(m?.info?.meta?.hidden ?? false)).map((m) => m.id);
	const getDefaultModelIds = () =>
		$config?.default_models ? $config.default_models.split(',') : [];
	const normalizeSelectedModels = (modelIds: string[] = []) => {
		const availableModels = getAvailableModelIds();
		const defaultModels = getDefaultModelIds();
		let normalized = (modelIds ?? []).filter(
			(modelId) => modelId && availableModels.includes(modelId)
		);

		if (normalized.length === 0 && $settings?.models?.length) {
			normalized = $settings.models.filter((modelId) => availableModels.includes(modelId));
		}
		if (normalized.length === 0 && defaultModels.length > 0) {
			normalized = defaultModels.filter((modelId) => availableModels.includes(modelId));
		}
		if (normalized.length === 0) {
			normalized = availableModels.length > 0 ? [availableModels[0]] : [''];
		}

		return normalized;
	};

	$: {
		const modelSearchParam =
			$page.url.searchParams.get('models') || $page.url.searchParams.get('model');

		if (
			chatIdProp === '' &&
			$models.length > 0 &&
			!selectedModels?.some((modelId) => modelId) &&
			!modelSearchParam
		) {
			const fallbackModels = normalizeSelectedModels(selectedModels);
			if (!equal(fallbackModels, selectedModels)) {
				selectedModels = fallbackModels;
			}
		}
	}

	const estimateTokens = (value) => {
		if (value === null || value === undefined || value === '') {
			return 0;
		}
		if (typeof value !== 'string') {
			try {
				value = JSON.stringify(value);
			} catch {
				value = String(value);
			}
		}
		return Math.max(1, Math.floor(value.length / 4));
	};

	const estimateMessagesTokens = (messages) =>
		messages.reduce((total, message) => {
			let next = total + 4 + estimateTokens(message.content);
			next += estimateTokens(message.output);
			next += estimateTokens(message.tool_calls);
			next += estimateTokens(message.files);
			return next;
		}, 0);

	$: contextCompactionEnabled = Boolean($config?.features?.enable_context_compaction);

	const getContextThreshold = () => {
		const chatThreshold = Number(params?.compact_token_threshold);
		if (Number.isFinite(chatThreshold) && chatThreshold > 0) {
			return chatThreshold;
		}

		const modelId = atSelectedModel?.id ?? selectedModels.find((id) => id);
		const model = $models.find((item) => item.id === modelId);
		const threshold = Number(model?.info?.params?.compact_token_threshold);
		return Number.isFinite(threshold) && threshold > 0 ? threshold : null;
	};

	const getContextUsage = () => {
		if (!history?.currentId) {
			return null;
		}

		const messages = createMessagesList(history, history.currentId);
		const threshold = contextCompactionEnabled
			? (getContextThreshold() ?? serverContextUsage?.threshold ?? null)
			: null;
		const systemTokens = estimateTokens($settings?.system ?? '');
		let estimatedTokens = systemTokens;
		let hasUsageCheckpoint = false;
		let summary = '';
		let startIdx = 0;

		for (let idx = 0; idx < messages.length; idx += 1) {
			const value = messages[idx]?.contextSummary ?? messages[idx]?.context_summary;
			if (typeof value === 'string' && value.trim()) {
				summary = value;
				startIdx = idx;
			}
		}

		const activeMessages = messages.slice(startIdx);

		for (let idx = activeMessages.length - 1; idx >= 0; idx -= 1) {
			const usage = activeMessages[idx]?.usage ?? activeMessages[idx]?.info?.usage;
			const inputTokens = usage?.input_tokens ?? usage?.prompt_tokens;
			if (inputTokens) {
				hasUsageCheckpoint = true;
				estimatedTokens =
					Number(inputTokens || 0) +
					Number(usage.output_tokens ?? usage.completion_tokens ?? 0) +
					estimateMessagesTokens(activeMessages.slice(idx + 1));
				break;
			}
		}

		if (!hasUsageCheckpoint) {
			estimatedTokens += estimateTokens(summary) + estimateMessagesTokens(activeMessages);
		}

		return {
			tokens: estimatedTokens,
			estimated_tokens: estimatedTokens,
			threshold,
			percent: threshold > 0 ? Math.max(0, Math.round((estimatedTokens / threshold) * 100)) : null,
			source: 'estimated'
		};
	};

	$: contextUsage = getContextUsage() ?? (contextCompactionEnabled ? serverContextUsage : null);
	$: embeddedHeaderTitle = embeddedTitle || $chatTitle || $i18n.t('Chat');

	let selectedToolIds: string[] = [];
	let selectedSkillIds: string[] = [];
	let selectedFilterIds: string[] = [];
	let pendingOAuthTools: ConversationModePendingOAuthTool[] = [];

	let imageGenerationEnabled = false;
	let imageGenerationUserOverride: boolean | null = null;
	let webSearchEnabled = false;
	let codeInterpreterEnabled = false;
	let reasoningEffort: ReasoningEffort = 'medium';
	let modeProfileDraftController = createConversationModeProfileDraftController();
	let modeProfileDraftId = '';
	let modeProfileInitializedDraftId = '';
	let modeProfileRevisionId: string | null = null;
	let modeProfileWarningSignature = '';
	let modeProfileCapabilityAuthorityController =
		createConversationModeCapabilityAuthorityController({
			existingChat: false
		});
	let modeProfileCapabilityAuthority: ConversationModeCapabilityAuthority =
		modeProfileCapabilityAuthorityController.snapshot();
	let modeProfileCapabilityOverrideFields: ConversationModeCapabilityOverrideField[] | null = null;
	let modeProfileControlsReady = false;
	let modeProfileBoundTerminalId: string | null = null;
	let latestModeProfileDraftInput: any = null;
	let modeProfileSetupPromise: Promise<void> | null = null;
	const modeProfileExternalCatalogCache = createConversationModeExternalCatalogCache();
	const modeProfileExternalCatalogPromises = new Map<string, Promise<boolean>>();
	let modeProfileCatalogGeneration = 0;
	const invalidateModeProfileCatalogGeneration = () => {
		modeProfileCatalogGeneration += 1;
	};
	const isModeProfileCatalogGenerationCurrent = (expectedGeneration?: number) =>
		expectedGeneration === undefined || expectedGeneration === modeProfileCatalogGeneration;
	type ModeProfileExternalCatalogRequest = {
		fingerprint: string;
		directToolServersPermitted: boolean;
		terminalCandidateIds: string[];
		configuredToolServers: any[];
		configuredTerminalServers: any[];
		configuredDirectTerminalIds: string[];
		currentToolServers: ConversationModeToolServer[];
		currentTerminalServers: ConversationModeToolServer[];
	};
	const MODE_PROFILE_EXTERNAL_CATALOG_TIMEOUT_MS = 5000;
	const MODE_PROFILE_EXTERNAL_CATALOG_MAX_AGE_MS = 60_000;
	const MODE_PROFILE_EXTERNAL_CATALOG_RETRY_MS = 10_000;
	let webSearchActive = false;
	let showWebSearchConfirm = false;
	let pendingWebSearchPrompt: string | null = null;
	let webSearchConfirmed = false;

	$: {
		const currentModels = atSelectedModel?.id ? [atSelectedModel.id] : selectedModels;
		const allModelsSupportWebSearch =
			currentModels.filter(
				(model) => $models.find((m) => m.id === model)?.info?.meta?.capabilities?.web_search ?? true
			).length === currentModels.length;

		webSearchActive = Boolean(
			$config?.features?.enable_web_search &&
			($user?.role === 'admin' || $user?.permissions?.features?.web_search) &&
			(webSearchEnabled ||
				(!modeProfileRevisionId &&
					allModelsSupportWebSearch &&
					($settings?.webSearch ?? false) === 'always'))
		);
	}

	const openWebSearchConfirm = () => {
		window.setTimeout(() => {
			showWebSearchConfirm = true;
		}, 0);
	};

	const handleWebSearchToggle = (enabled: boolean) => {
		if (enabled && $config?.features?.enable_web_search_confirmation && !webSearchConfirmed) {
			webSearchEnabled = false;
			pendingWebSearchPrompt = null;
			openWebSearchConfirm();
		}
	};

	const resetWebSearchConfirmation = () => {
		webSearchConfirmed = false;
		pendingWebSearchPrompt = null;
		showWebSearchConfirm = false;
	};

	$: if (!webSearchActive) {
		resetWebSearchConfirmation();
	}

	$: if (modeProfileControlsReady && $selectedTerminalId !== modeProfileBoundTerminalId) {
		modeProfileCapabilityAuthority = modeProfileCapabilityAuthorityController.markExplicit();
		modeProfileCapabilityOverrideFields = null;
		modeProfileBoundTerminalId = $selectedTerminalId;
		saveModeProfileCapabilityAuthority();
	}

	let showCommands = false;

	let generating = false;
	let dragged = false;
	let generationController = null;
	let contextCompactionToastId = null;

	let chat = null;
	let tags = [];

	// Read-only when viewing someone else's chat (e.g. via shared folder access)
	$: readOnly = chat != null && chat.user_id !== $user?.id;

	let chatTasks = [];

	let history = {
		messages: {},
		currentId: null
	};
	$: conversationModeLocked = Boolean(
		$chatId || Object.values(history.messages).some((message: any) => message?.role === 'user')
	);

	let taskIds = null;

	// Chat Input
	let prompt = '';
	let chatFiles = [];
	let files = [];
	let params = {};
	let chatVariables = {};
	let showChatVariablesModal = false;
	let loadedChatIdProp = '';
	let currentDraftKey = '';

	const mergeChatVariableSchemas = (modelIds = [], availableModels = []) => {
		const byKey: Record<string, any> = {};
		const conflicts: any[] = [];

		for (const modelId of modelIds.filter(Boolean)) {
			const fields =
				availableModels.find((model) => model.id === modelId)?.info?.meta?.chat_variables_schema
					?.fields ?? [];
			for (const rawField of fields) {
				const field = {
					...rawField,
					type: rawField?.type ?? 'text',
					required: Boolean(rawField?.required)
				};
				if (!field?.key) continue;
				const { required, ...shape } = field;

				const existing = byKey[field.key];
				if (!existing) {
					byKey[field.key] = {
						field,
						modelIds: [modelId],
						shape
					};
					continue;
				}

				if (!equal(existing.shape, shape)) {
					conflicts.push({
						key: field.key,
						modelIds: [...existing.modelIds, modelId]
					});
					continue;
				}

				existing.field = {
					...existing.field,
					required: existing.field.required || field.required
				};
				existing.modelIds.push(modelId);
			}
		}

		return {
			fields: Object.values(byKey).map((item: any) => item.field),
			conflicts
		};
	};

	const hasValue = (value) => value !== undefined && value !== null && value !== '';

	const getChatVariablesForm = (modelIds = [], values = {}, availableModels = []) => {
		const { fields, conflicts } = mergeChatVariableSchemas(modelIds, availableModels);
		const empty =
			fields.length > 0 &&
			fields.every((field) => !hasValue(values?.[field.key]) && !hasValue(field.default));
		const missing = fields.some(
			(field) => field.required && !hasValue(values?.[field.key]) && !hasValue(field.default)
		);
		const variables = fields.reduce(
			(acc, field) => {
				const { key, ...inputField } = field;
				acc[key] = {
					...inputField,
					default: hasValue(values?.[key]) ? values[key] : inputField.default
				};
				return acc;
			},
			{} as Record<string, any>
		);

		return { conflicts, empty, missing, variables };
	};

	const saveChatVariables = async (values) => {
		chatVariables = { ...chatVariables, ...values };

		if ($chatId && !$temporaryChatEnabled && !isTemporaryChatId($chatId)) {
			const res = await updateChatById(localStorage.token, $chatId, {}, chatVariables).catch(
				(err) => {
					console.error('[chat variables save]', err);
					toast.error($i18n.t('Failed to save chat variables'));
					return null;
				}
			);
			if (res) chat = res;
		}
	};

	const mergeFiles = (current, incoming) => {
		const seen = new Set();
		return [...(incoming ?? []), ...(current ?? [])].filter((file) => {
			const key = `${file?.type ?? ''}:${file?.id ?? file?.url ?? file?.name ?? ''}`;
			if (seen.has(key)) return false;
			seen.add(key);
			return true;
		});
	};
	const withSelectedText = (text: string) =>
		embedded && selectedText?.trim()
			? `${text}\n\nSelected note text for replace_note_content operations:\n${selectedText.trim()}`
			: text;
	const noteChatDebug = (message: string, data: Record<string, unknown> = {}) => {
		if (!embedded) return;
		console.info('[note-chat]', message, {
			chatIdProp,
			activeChatId: $chatId,
			loading,
			...data
		});
	};

	$: if (chatIdProp && chatIdProp !== loadedChatIdProp) {
		noteChatDebug('chatIdProp changed; loading linked chat', {
			previousChatIdProp: loadedChatIdProp
		});
		loadedChatIdProp = chatIdProp;
		navigateHandler();
	}

	$: if (embedded && embeddedDraftKey && embeddedDraftKey !== currentDraftKey) {
		noteChatDebug('embedded draft requested', { embeddedDraftKey });
		currentDraftKey = embeddedDraftKey;
		initEmbeddedDraft();
	}

	let saveControlsTimer;
	$: if (!loading && !$temporaryChatEnabled && $chatId && params && chatFiles) {
		clearTimeout(saveControlsTimer);
		saveControlsTimer = setTimeout(saveControls, 400);
	}

	const navigateHandler = async () => {
		invalidateModeProfileCatalogGeneration();
		const expectedCatalogGeneration = modeProfileCatalogGeneration;
		// Mark the outgoing chat as read before loading the new one.
		// $chatId still holds the previous chat here — loadChat() updates it.
		if ($chatId && $chatId !== chatIdProp && !$temporaryChatEnabled) {
			noteChatDebug('marking outgoing chat read', { outgoingChatId: $chatId });
			updateLastReadAt($chatId);
		}

		clearTimeout(saveControlsTimer);
		await saveControls();
		if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
		loading = true;

		prompt = '';
		messageInput?.setText('');

		files = [];
		selectedToolIds = [];
		selectedSkillIds = [];
		selectedFilterIds = [];
		webSearchEnabled = false;
		imageGenerationEnabled = false;
		imageGenerationUserOverride = null;
		codeInterpreterEnabled = false;
		clearSelectedTerminal();
		modeProfileDraftId = '';
		modeProfileInitializedDraftId = '';
		modeProfileRevisionId = null;
		modeProfileWarningSignature = '';
		modeProfileControlsReady = false;
		modeProfileBoundTerminalId = null;
		modeProfileDraftController = createConversationModeProfileDraftController();
		modeProfileCapabilityAuthorityController = createConversationModeCapabilityAuthorityController({
			existingChat: false
		});
		modeProfileCapabilityAuthority = modeProfileCapabilityAuthorityController.snapshot();
		modeProfileCapabilityOverrideFields = null;
		reasoningEffort = 'medium';

		const storageChatInput = sessionStorage.getItem(
			`chat-input${chatIdProp ? `-${chatIdProp}` : ''}`
		);
		const restoredDraft = parseConversationModeDraft(storageChatInput);
		if (storageChatInput && !restoredDraft) {
			sessionStorage.removeItem(`chat-input${chatIdProp ? `-${chatIdProp}` : ''}`);
		}

		const loadedChat = chatIdProp ? await loadChat(chatIdProp, expectedCatalogGeneration) : false;
		if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
		if (chatIdProp && loadedChat) {
			const restoredCapabilitySnapshot = getConversationModeDraftCapabilitySnapshotForMode(
				restoredDraft,
				conversationMode,
				{ existingChat: true }
			);
			modeProfileCapabilityAuthorityController =
				createConversationModeCapabilityAuthorityController({
					existingChat: true,
					persistedAuthority: restoredCapabilitySnapshot?.authority
				});
			modeProfileCapabilityAuthority = modeProfileCapabilityAuthorityController.snapshot();

			let restoredLegacyCapabilities = false;
			if (restoredDraft && !$temporaryChatEnabled) {
				messageInput?.setText(restoredDraft.prompt);
				files = restoredDraft.files;
				modeProfileDraftId = `persistent:${chatIdProp}`;
				reasoningEffort = normalizeReasoningEffort(
					restoredDraft.reasoningEffort ?? restoredDraft.reasoningDepth
				);
				if (restoredCapabilitySnapshot) {
					await restoreModeProfileCapabilitySnapshot(
						restoredCapabilitySnapshot,
						expectedCatalogGeneration
					);
				} else {
					restoredLegacyCapabilities = await restoreLegacyModeProfileDraftCapabilities(
						isConversationModeDraftCompatible(restoredDraft, conversationMode)
							? restoredDraft
							: null,
						{
							preserveBoundDefaults: true,
							expectedCatalogGeneration
						}
					);
				}
				if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
			}
			if (restoredLegacyCapabilities) {
				await finalizeModeProfileCapabilitySnapshot(expectedCatalogGeneration);
				if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
			} else if (!restoredCapabilitySnapshot) {
				modeProfileBoundTerminalId = $selectedTerminalId;
				queueMicrotask(() => {
					if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
					modeProfileCapabilityAuthority = modeProfileCapabilityAuthorityController.observe({
						selectedToolIds,
						selectedSkillIds,
						selectedFilterIds,
						webSearchEnabled,
						codeInterpreterEnabled,
						imageGenerationEnabled
					});
					modeProfileControlsReady = true;
				});
			}

			await tick();
			if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
			loading = false;
			noteChatDebug('embedded chat loading false');
			window.setTimeout(() => scrollToBottom(), 0);

			await tick();
			if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;

			// Mark chat read when initially loading it
			if (chatIdProp && !$temporaryChatEnabled) {
				updateLastReadAt(chatIdProp);
			}

			// Process any queued requests if the chat is idle
			const lastMessage = history.currentId ? history.messages[history.currentId] : null;
			const isIdle = !lastMessage || lastMessage.role !== 'assistant' || lastMessage.done;
			if (isIdle) {
				await processNextInQueue(chatIdProp, expectedCatalogGeneration);
				if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
			}

			const chatInput = document.getElementById('chat-input');
			chatInput?.focus();
		} else if (!embedded) {
			await goto('/');
		} else {
			loading = false;
			console.warn('[note-chat] embedded load failed; clearing spinner', {
				chatIdProp,
				activeChatId: $chatId
			});
		}
	};

	const initEmbeddedDraft = async () => {
		clearTimeout(saveControlsTimer);
		await saveControls();

		if ($chatId && !$temporaryChatEnabled) {
			updateLastReadAt($chatId);
		}

		loading = true;
		loadedChatIdProp = '';
		chat = null;
		tags = [];
		taskIds = null;
		chatTasks = [];
		serverContextUsage = null;
		history = {
			messages: {},
			currentId: null
		};
		params = {};
		chatVariables = {};
		chatFiles = [];
		files = [];
		selectedToolIds = [];
		selectedSkillIds = [];
		selectedFilterIds = [];
		webSearchEnabled = false;
		imageGenerationEnabled = false;
		codeInterpreterEnabled = false;
		prompt = '';
		messageInput?.setText('');
		await chatId.set('');
		await chatTitle.set('');

		await setDefaults();
		loading = false;
		await tick();
		document.getElementById('chat-input')?.focus();
	};

	const onSelect = async (e) => {
		const { type, data } = e;

		if (type === 'prompt') {
			// Handle prompt selection
			messageInput?.setText(data, async () => {
				if (!($settings?.insertSuggestionPrompt ?? false)) {
					await tick();
					submitHandler(prompt);
				}
			});
		}
	};

	$: if (selectedModels && chatIdProp !== '') {
		saveSessionSelectedModels();
	}

	const saveSessionSelectedModels = () => {
		const selectedModelsString = JSON.stringify(selectedModels);
		if (
			selectedModels.length === 0 ||
			(selectedModels.length === 1 && selectedModels[0] === '') ||
			sessionStorage.selectedModels === selectedModelsString
		) {
			return;
		}
		sessionStorage.selectedModels = selectedModelsString;
		console.log('saveSessionSelectedModels', selectedModels, sessionStorage.selectedModels);
	};

	const continueOAuthRedirect = async (expectedCatalogGeneration?: number) => {
		if (pendingOAuthTools.length === 0) {
			sessionStorage.removeItem('oauthRedirectInProgressToolId');
			return;
		}

		if (chatIdProp) {
			return;
		}

		const nextTool = pendingOAuthTools[0];
		if (sessionStorage.getItem('oauthRedirectInProgressToolId') === nextTool.id) {
			sessionStorage.removeItem('oauthRedirectInProgressToolId');
			return;
		}

		saveSessionSelectedModels();
		await tick();
		if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
		initiateOAuthRedirect(nextTool);
	};

	let oldSelectedModelIds = [''];
	$: if (!equal(selectedModelIds, oldSelectedModelIds)) {
		onSelectedModelIdsChange();
	}

	const runModeProfileSetup = async (setup: () => Promise<void>) => {
		if (modeProfileSetupPromise) {
			await modeProfileSetupPromise;
		}
		const setupPromise = setup();
		modeProfileSetupPromise = setupPromise;
		try {
			await setupPromise;
		} finally {
			if (modeProfileSetupPromise === setupPromise) {
				modeProfileSetupPromise = null;
			}
		}
	};

	const onSelectedModelIdsChange = async () => {
		if (modeProfileCapabilityAuthority === 'inherit_bound') {
			oldSelectedModelIds = structuredClone(selectedModelIds);
			return;
		}
		if (!modeProfileControlsReady) {
			oldSelectedModelIds = structuredClone(selectedModelIds);
			return;
		}
		const expectedCatalogGeneration = modeProfileCatalogGeneration;
		await runModeProfileSetup(async () => {
			if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
			if (
				(modeProfileInitializedDraftId && modeProfileInitializedDraftId === modeProfileDraftId) ||
				modeProfileRevisionId
			) {
				await revalidateModeProfileCapabilities({ expectedCatalogGeneration });
			} else {
				modeProfileControlsReady = false;
				await resetInput({ initialize: Boolean(modeProfileDraftId) });
				if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
				await finalizeModeProfileCapabilitySnapshot(expectedCatalogGeneration);
			}
		});
		oldSelectedModelIds = structuredClone(selectedModelIds);
	};

	const resetInput = async ({ initialize = false }: { initialize?: boolean } = {}) => {
		selectedToolIds = [];
		selectedSkillIds = [];
		selectedFilterIds = [];
		pendingOAuthTools = [];
		webSearchEnabled = false;
		imageGenerationEnabled = false;
		imageGenerationUserOverride = null;
		codeInterpreterEnabled = false;
		clearSelectedTerminal();
		if (initialize) modeProfileCapabilityOverrideFields = null;

		if (initialize && selectedModelIds.filter((id) => id).length > 0) {
			await setDefaults();
		}
	};

	const clearSelectedTerminal = () => {
		selectedTerminalId.set(null);
		const configuredTerminalServers = (($settings as any)?.terminalServers ?? []) as Array<{
			enabled?: boolean;
			[key: string]: unknown;
		}>;

		if (configuredTerminalServers.some((server) => server.enabled)) {
			settings.set({
				...($settings as any),
				terminalServers: configuredTerminalServers.map((server) => ({ ...server, enabled: false }))
			} as any);
		}
	};

	const getModeProfile = (): ConversationModeProfilePublic | null => {
		const profiles = ($config as { conversation_mode_profiles?: ConversationModeProfilePublic[] })
			?.conversation_mode_profiles;
		return profiles?.find((profile) => profile.mode === conversationMode) ?? null;
	};

	const getModeProfileAvailableFeatureIds = () => {
		const features = ($config?.features ?? {}) as Record<string, boolean | undefined>;
		const canUse = (feature: string) =>
			$user?.role === 'admin' || ($user?.permissions?.features?.[feature] ?? false);
		return [
			...(features.enable_web_search && canUse('web_search') ? ['web_search'] : []),
			...(features.enable_code_interpreter && canUse('code_interpreter')
				? ['code_interpreter']
				: []),
			...(features.enable_image_generation && canUse('image_generation')
				? ['image_generation']
				: [])
		];
	};

	const getModeProfileTerminalCandidates = (
		modelOverride: Model | undefined = undefined,
		requestContext?: ConversationModeRequestContext<any>
	) => {
		const model = (requestContext?.model ??
			modelOverride ??
			atSelectedModel ??
			$models.find((item) => item.id === selectedModels[0])) as any;
		const profileTerminalId =
			requestContext?.profile?.defaults?.terminal_id ?? getModeProfile()?.defaults?.terminal_id;
		return [
			...new Set([
				requestContext?.selections.terminalId ?? $selectedTerminalId,
				typeof profileTerminalId === 'string' ? profileTerminalId : null,
				model?.info?.meta?.terminalId
			])
		].filter((id): id is string => typeof id === 'string' && id.length > 0);
	};

	const getModeProfileExternalCatalogRequest = (
		modelOverride: Model | undefined = undefined,
		requestContext?: ConversationModeRequestContext<any>
	): ModeProfileExternalCatalogRequest => {
		const directToolServersPermitted =
			requestContext?.directToolServersPermitted ?? isDirectToolServersPermitted($user);
		const terminalCandidateIds = getModeProfileTerminalCandidates(modelOverride, requestContext);
		const configuredToolServers = directToolServersPermitted
			? structuredClone((($settings as any)?.toolServers ?? []) as any[])
			: [];
		const configuredTerminalSettings = structuredClone(
			(($settings as any)?.terminalServers ?? []) as any[]
		);
		const configuredDirectTerminalIds = configuredTerminalSettings
			.map((server) => server?.url)
			.filter((id): id is string => typeof id === 'string' && id.length > 0);
		const terminalCandidates = new Set(terminalCandidateIds);
		const configuredTerminalServers = directToolServersPermitted
			? configuredTerminalSettings
					.filter((server) => terminalCandidates.has(server.url))
					.map((server) => ({
						url: server.url,
						auth_type: server.auth_type ?? 'bearer',
						key: server.key ?? '',
						path: server.path ?? '/openapi.json',
						config: { enable: true }
					}))
			: [];
		const fingerprint = getConversationModeExternalCatalogFingerprint({
			userId: $user?.id ?? null,
			directToolServersPermitted,
			configuredToolServers,
			configuredTerminalServers: configuredTerminalSettings,
			terminalCandidateIds
		});
		return {
			fingerprint,
			directToolServersPermitted,
			terminalCandidateIds,
			configuredToolServers,
			configuredTerminalServers,
			configuredDirectTerminalIds,
			currentToolServers: structuredClone(($toolServers ?? []) as ConversationModeToolServer[]),
			currentTerminalServers: structuredClone(
				($terminalServers ?? []) as ConversationModeToolServer[]
			)
		};
	};

	const getModeProfileExternalCatalogView = (request: ModeProfileExternalCatalogRequest) => {
		const state = modeProfileExternalCatalogCache.snapshot(request.fingerprint);
		return {
			state,
			toolServers: state.catalog?.toolServers ?? request.currentToolServers,
			terminalServers: state.catalog?.terminalServers ?? request.currentTerminalServers,
			directToolServerCatalogReady: state.catalog !== null
		};
	};

	const getModeProfileAvailability = (
		modelOverride: Model | undefined = undefined,
		requestContext?: ConversationModeRequestContext<any>,
		externalCatalogRequest: ModeProfileExternalCatalogRequest = getModeProfileExternalCatalogRequest(
			modelOverride,
			requestContext
		)
	): ConversationModeProfileAvailability => {
		const model = (requestContext?.model ??
			modelOverride ??
			atSelectedModel ??
			$models.find((item) => item.id === selectedModels[0])) as any;
		const catalogView = getModeProfileExternalCatalogView(externalCatalogRequest);
		const availableTerminals = catalogView.terminalServers as any[];
		const configuredTerminalServers = externalCatalogRequest.configuredTerminalServers;
		const availableTools = ($tools ?? []) as any[];
		const availableSkills = ($skills ?? []) as any[];
		const directTerminalPermitted = externalCatalogRequest.directToolServersPermitted;

		return {
			terminalIds: [
				...availableTerminals
					.map((server) => (server.id ? server.id : directTerminalPermitted ? server.url : null))
					.filter((id): id is string => typeof id === 'string' && id.length > 0),
				...(!catalogView.directToolServerCatalogReady && directTerminalPermitted
					? configuredTerminalServers
					: []
				)
					.map((server) => server.url)
					.filter((id): id is string => typeof id === 'string' && id.length > 0),
				...(!catalogView.directToolServerCatalogReady
					? filterConversationModeTerminalCandidateIds({
							candidateIds: externalCatalogRequest.terminalCandidateIds,
							configuredDirectTerminalIds: externalCatalogRequest.configuredDirectTerminalIds,
							directToolServersPermitted: directTerminalPermitted
						})
					: [])
			],
			toolIds: getConversationModeAvailableToolIds({
				tools: availableTools,
				toolServers: catalogView.toolServers,
				configuredToolServers: externalCatalogRequest.configuredToolServers,
				directToolServerCatalogReady: catalogView.directToolServerCatalogReady,
				directToolServersPermitted: directTerminalPermitted
			}),
			skillIds: availableSkills
				.filter((skill) => skill.is_active)
				.map((skill) => skill.id)
				.filter((id): id is string => Boolean(id)),
			filterIds: ((model?.filters ?? []) as Array<{ id?: string }>)
				.map((filter) => filter.id)
				.filter((id): id is string => Boolean(id)),
			featureIds:
				requestContext?.featureState.availableFeatureIds ?? getModeProfileAvailableFeatureIds()
		};
	};

	/** Check whether a terminal ID references an available system or direct terminal. */
	const isTerminalAvailable = (tid: string): boolean =>
		getModeProfileAvailability().terminalIds.includes(tid);

	const getModeProfileSelectedToolIds = ({
		includePendingOAuthTools = true
	}: { includePendingOAuthTools?: boolean } = {}) => [
		...new Set([
			...selectedToolIds,
			...(includePendingOAuthTools ? pendingOAuthTools.map((tool) => tool.id) : [])
		])
	];

	const getModeProfileSelections = (
		options: { includePendingOAuthTools?: boolean } = {}
	): ConversationModeProfileSelections => ({
		terminalId: $selectedTerminalId ?? null,
		toolIds: getModeProfileSelectedToolIds(options),
		skillIds: [...selectedSkillIds],
		filterIds: [...selectedFilterIds],
		featureIds: [
			...(webSearchEnabled ? ['web_search'] : []),
			...(imageGenerationEnabled ? ['image_generation'] : []),
			...(codeInterpreterEnabled ? ['code_interpreter'] : [])
		]
	});

	const withModeProfileExternalCatalogTimeout = <T,>(promise: Promise<T>, label: string) =>
		new Promise<T>((resolve, reject) => {
			const timeout = window.setTimeout(
				() => reject(new Error(`${label} timed out`)),
				MODE_PROFILE_EXTERNAL_CATALOG_TIMEOUT_MS
			);
			promise.then(
				(value) => {
					window.clearTimeout(timeout);
					resolve(value);
				},
				(error) => {
					window.clearTimeout(timeout);
					reject(error);
				}
			);
		});

	const refreshModeProfileExternalCatalogs = (
		request: ModeProfileExternalCatalogRequest,
		{ force = false }: { force?: boolean } = {}
	) => {
		const { fingerprint } = request;
		const inFlight = modeProfileExternalCatalogPromises.get(fingerprint);
		if (inFlight) return inFlight;
		if (!modeProfileExternalCatalogCache.begin(fingerprint, { force })) {
			return Promise.resolve(false);
		}

		const loadPromise = (async () => {
			try {
				const [loadedToolServers, loadedDirectTerminals, systemTerminals] = await Promise.all([
					withModeProfileExternalCatalogTimeout(
						getToolServersData(request.configuredToolServers),
						'tool server discovery'
					),
					withModeProfileExternalCatalogTimeout(
						getToolServersData(request.configuredTerminalServers),
						'direct terminal discovery'
					),
					withModeProfileExternalCatalogTimeout(
						getTerminalServers(localStorage.token, { throwOnError: true }),
						'system terminal discovery'
					)
				]);
				modeProfileExternalCatalogCache.succeed(fingerprint, {
					toolServers: (loadedToolServers ?? []).filter((server): server is Record<string, any> =>
						Boolean(server && !server.error)
					),
					terminalServers: [
						...(loadedDirectTerminals ?? []).filter((server): server is Record<string, any> =>
							Boolean(server && !server.error)
						),
						...(systemTerminals ?? []).map((terminal) => ({
							id: terminal.id,
							url: `${WEBUI_API_BASE_URL}/terminals/${terminal.id}`,
							name: terminal.name,
							key: localStorage.token
						}))
					]
				});
				return true;
			} catch (error) {
				modeProfileExternalCatalogCache.fail(fingerprint, error);
				console.error('[mode profile catalogs]', error);
				return false;
			} finally {
				modeProfileExternalCatalogPromises.delete(fingerprint);
			}
		})();

		modeProfileExternalCatalogPromises.set(fingerprint, loadPromise);
		return loadPromise;
	};

	const shouldRefreshModeProfileExternalCatalog = (request: ModeProfileExternalCatalogRequest) =>
		modeProfileExternalCatalogCache.shouldRefresh(request.fingerprint, {
			maxAgeMs: MODE_PROFILE_EXTERNAL_CATALOG_MAX_AGE_MS,
			retryAfterMs: MODE_PROFILE_EXTERNAL_CATALOG_RETRY_MS
		});

	const ensureModeProfileCatalogsLoaded = async () => {
		if (!$tools) tools.set(await getTools(localStorage.token));
		if (!$functions) functions.set(await getFunctions(localStorage.token));
		if (!$skills) skills.set(await getSkills(localStorage.token));
	};

	const showModeProfileWarnings = (warnings: any[]) => {
		const warningSignature = JSON.stringify(warnings);
		if (warnings.length > 0 && warningSignature !== modeProfileWarningSignature) {
			modeProfileWarningSignature = warningSignature;
			toast.warning(
				$i18n.t('Some conversation capabilities are unavailable for the selected model')
			);
		}
	};

	const applyModeProfileResolution = async (
		resolution: any,
		phase: 'initialize' | 'model_change',
		expectedCatalogGeneration?: number
	) => {
		const oauthPartition = partitionConversationModeOAuthTools(
			resolution.effective.toolIds,
			$tools ?? []
		);
		selectedToolIds = oauthPartition.selectedToolIds;
		pendingOAuthTools = oauthPartition.pendingOAuthTools;
		selectedSkillIds = resolution.effective.skillIds;
		selectedFilterIds = resolution.effective.filterIds;
		webSearchEnabled = resolution.effective.featureIds.includes('web_search');
		imageGenerationEnabled = resolution.effective.featureIds.includes('image_generation');
		codeInterpreterEnabled = resolution.effective.featureIds.includes('code_interpreter');

		const profile = getModeProfile();
		if (
			phase === 'initialize' &&
			profile &&
			Object.prototype.hasOwnProperty.call(profile.defaults, 'feature_ids')
		) {
			imageGenerationUserOverride = imageGenerationEnabled;
		}

		clearSelectedTerminal();
		if (resolution.effective.terminalId) {
			selectedTerminalId.set(resolution.effective.terminalId);
			codeInterpreterEnabled = false;
		}

		showModeProfileWarnings(resolution.warnings);
		await continueOAuthRedirect(expectedCatalogGeneration);
	};

	const applyModeProfileInitialization = async (
		externalCatalogRequest = getModeProfileExternalCatalogRequest(),
		expectedCatalogGeneration?: number
	) => {
		if (!modeProfileDraftId) return;
		const model = atSelectedModel ?? $models.find((item) => item.id === selectedModels[0]);
		const snapshot = modeProfileDraftController.initialize(
			modeProfileDraftId,
			resolveConversationModeProfile({
				mode: conversationMode,
				profile: getModeProfile(),
				model,
				available: getModeProfileAvailability(undefined, undefined, externalCatalogRequest),
				phase: 'initialize'
			})
		);
		if (!snapshot.applied) return;
		modeProfileInitializedDraftId = modeProfileDraftId;
		modeProfileRevisionId = snapshot.revisionHint;
		await applyModeProfileResolution(snapshot, 'initialize', expectedCatalogGeneration);
	};

	const applyModeProfileModelChange = async (
		externalCatalogRequest = getModeProfileExternalCatalogRequest(),
		expectedCatalogGeneration?: number
	) => {
		const model = atSelectedModel ?? $models.find((item) => item.id === selectedModels[0]);
		const snapshot = modeProfileDraftController.applyModelChange(
			resolveConversationModeProfile({
				mode: conversationMode,
				profile: getModeProfile(),
				model,
				available: getModeProfileAvailability(undefined, undefined, externalCatalogRequest),
				currentSelections: getModeProfileSelections(),
				phase: 'model_change'
			})
		);
		modeProfileRevisionId = snapshot.revisionHint;
		await applyModeProfileResolution(snapshot, 'model_change', expectedCatalogGeneration);
	};

	const revalidateModeProfileCapabilities = async ({
		refreshExternalCatalog = true,
		expectedCatalogGeneration
	}: { refreshExternalCatalog?: boolean; expectedCatalogGeneration?: number } = {}) => {
		if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
		const restoreControlsReady = modeProfileControlsReady;
		modeProfileControlsReady = false;
		try {
			await ensureModeProfileCatalogsLoaded();
			if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
			const externalCatalogRequest = getModeProfileExternalCatalogRequest();
			if (
				refreshExternalCatalog &&
				shouldRefreshModeProfileExternalCatalog(externalCatalogRequest)
			) {
				const state = modeProfileExternalCatalogCache.snapshot(externalCatalogRequest.fingerprint);
				await refreshModeProfileExternalCatalogs(externalCatalogRequest, {
					force: state.status !== 'idle'
				});
			}
			if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
			await applyModeProfileModelChange(externalCatalogRequest, expectedCatalogGeneration);
			if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
			modeProfileBoundTerminalId = $selectedTerminalId;
			modeProfileCapabilityAuthority = modeProfileCapabilityAuthorityController.rebase({
				selectedToolIds,
				selectedSkillIds,
				selectedFilterIds,
				webSearchEnabled,
				codeInterpreterEnabled,
				imageGenerationEnabled
			});
		} finally {
			if (
				restoreControlsReady &&
				isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)
			) {
				modeProfileControlsReady = true;
			}
		}
	};

	const triggerModeProfileExternalCatalogRefresh = (
		externalCatalogRequest: ModeProfileExternalCatalogRequest
	) => {
		const state = modeProfileExternalCatalogCache.snapshot(externalCatalogRequest.fingerprint);
		if (!shouldRefreshModeProfileExternalCatalog(externalCatalogRequest)) return;
		const refreshGeneration = modeProfileCatalogGeneration;
		const hadSuccessfulCatalog = state.catalog !== null;
		const previousCatalog = state.catalog;
		void refreshModeProfileExternalCatalogs(externalCatalogRequest, {
			force: state.status !== 'idle'
		}).then((succeeded) => {
			if (refreshGeneration !== modeProfileCatalogGeneration) return;
			const nextCatalog = modeProfileExternalCatalogCache.snapshot(
				externalCatalogRequest.fingerprint
			).catalog;
			if (
				succeeded &&
				(!hadSuccessfulCatalog || !equal(previousCatalog, nextCatalog)) &&
				modeProfileControlsReady &&
				modeProfileCapabilityAuthority !== 'inherit_bound' &&
				getModeProfileExternalCatalogRequest().fingerprint === externalCatalogRequest.fingerprint
			) {
				void runModeProfileSetup(() =>
					revalidateModeProfileCapabilities({
						refreshExternalCatalog: false,
						expectedCatalogGeneration: refreshGeneration
					})
				);
			}
		});
	};

	const resolveModeProfileRequest = async (
		requestContext: ConversationModeRequestContext<any>,
		externalCatalogRequest: ModeProfileExternalCatalogRequest
	) => {
		triggerModeProfileExternalCatalogRefresh(externalCatalogRequest);
		await ensureModeProfileCatalogsLoaded();
		const catalogView = getModeProfileExternalCatalogView(externalCatalogRequest);
		const resolution = resolveConversationModeRequestCapabilities({
			authority: requestContext.authority,
			mode: requestContext.mode,
			profile: requestContext.profile,
			model: requestContext.model,
			available: getModeProfileAvailability(
				requestContext.model as Model,
				requestContext,
				externalCatalogRequest
			),
			currentSelections: requestContext.selections
		});
		showModeProfileWarnings(resolution.warnings);
		return { ...resolution, catalogView };
	};

	const finalizeModeProfileCapabilitySnapshot = async (
		expectedCatalogGeneration = modeProfileCatalogGeneration
	) => {
		if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
		modeProfileBoundTerminalId = $selectedTerminalId;
		modeProfileCapabilityAuthority = modeProfileCapabilityAuthorityController.rebase({
			selectedToolIds,
			selectedSkillIds,
			selectedFilterIds,
			webSearchEnabled,
			codeInterpreterEnabled,
			imageGenerationEnabled
		});
		modeProfileControlsReady = true;
		await persistFinalizedModeProfileDraftSnapshot(expectedCatalogGeneration);
	};

	const restoreModeProfileCapabilitySnapshot = async (
		snapshot: ConversationModeDraftCapabilitySnapshot,
		expectedCatalogGeneration = modeProfileCatalogGeneration
	) => {
		await runModeProfileSetup(async () => {
			if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
			modeProfileControlsReady = false;
			modeProfileCapabilityOverrideFields = snapshot.overrideFields
				? [...snapshot.overrideFields]
				: null;
			selectedToolIds = [...snapshot.selections.toolIds];
			selectedSkillIds = [...snapshot.selections.skillIds];
			selectedFilterIds = [...snapshot.selections.filterIds];
			webSearchEnabled = snapshot.selections.featureIds.includes('web_search');
			codeInterpreterEnabled = snapshot.selections.featureIds.includes('code_interpreter');
			imageGenerationEnabled = snapshot.selections.featureIds.includes('image_generation');
			imageGenerationUserOverride = imageGenerationEnabled;
			clearSelectedTerminal();
			selectedTerminalId.set(snapshot.selections.terminalId);

			await revalidateModeProfileCapabilities({
				expectedCatalogGeneration: expectedCatalogGeneration
			});
			if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
			await finalizeModeProfileCapabilitySnapshot(expectedCatalogGeneration);
		});
	};

	const restoreLegacyModeProfileDraftCapabilities = async (
		draft: any,
		{
			preserveBoundDefaults = false,
			expectedCatalogGeneration = modeProfileCatalogGeneration
		}: { preserveBoundDefaults?: boolean; expectedCatalogGeneration?: number } = {}
	) => {
		if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return false;
		const positiveMigration = migrateConversationModeLegacyDraftCapabilities(draft, {
			terminalId: null,
			toolIds: [],
			skillIds: [],
			filterIds: [],
			featureIds: []
		});
		if (!positiveMigration) return false;

		const model = atSelectedModel ?? $models.find((item) => item.id === selectedModels[0]);
		if (!model) return false;
		await ensureModeProfileCatalogsLoaded();
		if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return false;
		const externalCatalogRequest = getModeProfileExternalCatalogRequest(model);
		const externalCatalogState = modeProfileExternalCatalogCache.snapshot(
			externalCatalogRequest.fingerprint
		);
		await refreshModeProfileExternalCatalogs(externalCatalogRequest, {
			force: externalCatalogState.status === 'error'
		});
		if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return false;
		const available = getModeProfileAvailability(model, undefined, externalCatalogRequest);
		const initializedSelections = preserveBoundDefaults
			? { terminalId: null, toolIds: [], skillIds: [], filterIds: [], featureIds: [] }
			: resolveConversationModeProfile({
					mode: conversationMode,
					profile: getModeProfile(),
					model,
					available,
					phase: 'initialize'
				}).effective;
		const migrated = migrateConversationModeLegacyDraftCapabilities(draft, initializedSelections);
		if (!migrated) return false;
		const revalidated = resolveConversationModeProfile({
			mode: conversationMode,
			profile: getModeProfile(),
			model,
			available,
			currentSelections: migrated.selections,
			phase: 'model_change'
		});

		modeProfileControlsReady = false;
		modeProfileCapabilityOverrideFields = preserveBoundDefaults
			? [...(migrated.overrideFields ?? [])]
			: null;
		modeProfileCapabilityAuthority = modeProfileCapabilityAuthorityController.markExplicit();
		await applyModeProfileResolution(revalidated, 'model_change', expectedCatalogGeneration);
		if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return false;
		imageGenerationUserOverride = imageGenerationEnabled;
		return true;
	};

	const hasImageGenerationAccess = () =>
		$user?.role === 'admin' || ($user?.permissions?.features?.image_generation ?? false);

	const getPrimaryImageGenerationModel = () =>
		atSelectedModel ?? $models.find((m) => m.id === selectedModels[0]);

	const beginModeProfileDraft = ({
		restoreRootDraft = true
	}: { restoreRootDraft?: boolean } = {}) => {
		invalidateModeProfileCatalogGeneration();
		const storedRootDraft =
			restoreRootDraft && !chatIdProp ? sessionStorage.getItem('chat-input') : null;
		const restoredRootDraft = parseConversationModeDraft(storedRootDraft);
		const restoredRootCapabilitySnapshot = getConversationModeDraftCapabilitySnapshotForMode(
			restoredRootDraft,
			conversationMode,
			{ existingChat: false }
		);
		if (storedRootDraft && !restoredRootDraft) {
			sessionStorage.removeItem('chat-input');
		}
		modeProfileDraftId = `draft:${uuidv4()}`;
		modeProfileInitializedDraftId = '';
		modeProfileRevisionId =
			restoredRootCapabilitySnapshot && typeof restoredRootDraft?.modeProfileRevisionId === 'string'
				? restoredRootDraft.modeProfileRevisionId
				: null;
		modeProfileWarningSignature = '';
		modeProfileControlsReady = false;
		modeProfileDraftController = createConversationModeProfileDraftController();
		modeProfileCapabilityAuthorityController = createConversationModeCapabilityAuthorityController({
			existingChat: false,
			persistedAuthority: restoredRootCapabilitySnapshot?.authority
		});
		modeProfileCapabilityAuthority = modeProfileCapabilityAuthorityController.snapshot();
		modeProfileCapabilityOverrideFields = null;
		modeProfileDraftController.hydrateRevisionHint(modeProfileRevisionId);
		clearSelectedTerminal();
		return restoredRootDraft;
	};

	const transitionConversationMode = async (nextMode: ConversationMode) => {
		if (conversationModeLocked || nextMode === conversationMode) return;
		loading = true;
		let expectedCatalogGeneration = modeProfileCatalogGeneration;
		try {
			await runModeProfileSetup(async () => {
				if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
				conversationMode = nextMode;
				beginModeProfileDraft({ restoreRootDraft: false });
				expectedCatalogGeneration = modeProfileCatalogGeneration;
				await resetInput({ initialize: true });
				if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
				await finalizeModeProfileCapabilitySnapshot(expectedCatalogGeneration);
			});
		} finally {
			if (isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) {
				loading = false;
			}
		}
	};

	const bindCanonicalModeProfileRevision = (revisionId: unknown) => {
		modeProfileRevisionId = modeProfileDraftController.bindCanonicalRevision(
			typeof revisionId === 'string' ? revisionId : null
		).revisionHint;
	};

	let settingDefaultsPromise: { generation: number; promise: Promise<void> } | null = null;
	const setDefaults = async (expectedCatalogGeneration = modeProfileCatalogGeneration) => {
		if (settingDefaultsPromise) {
			const inFlight = settingDefaultsPromise;
			await inFlight.promise;
			if (
				inFlight.generation === expectedCatalogGeneration ||
				!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)
			) {
				return;
			}
		}
		if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
		const defaultsPromise = (async () => {
			await ensureModeProfileCatalogsLoaded();
			if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
			const externalCatalogRequest = getModeProfileExternalCatalogRequest();
			const externalCatalogState = modeProfileExternalCatalogCache.snapshot(
				externalCatalogRequest.fingerprint
			);
			if (shouldRefreshModeProfileExternalCatalog(externalCatalogRequest)) {
				await refreshModeProfileExternalCatalogs(externalCatalogRequest, {
					force: externalCatalogState.status !== 'idle'
				});
			}
			if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
			if (selectedModelIds.filter((id) => id).length !== 1 && !atSelectedModel) {
				return;
			}

			const model = atSelectedModel ?? $models.find((m) => m.id === selectedModelIds[0]);
			if (model) {
				if (model.info?.meta?.toolIds) {
					const availableTools = ($tools ?? []) as any[];
					const defaultIds = [
						...new Set(
							[...(model.info.meta.toolIds ?? [])].filter((id) =>
								availableTools.find((tool) => tool.id === id)
							)
						)
					];
					const oauthPartition = partitionConversationModeOAuthTools(defaultIds, availableTools);
					selectedToolIds = oauthPartition.selectedToolIds;
					pendingOAuthTools = oauthPartition.pendingOAuthTools;
					await continueOAuthRedirect(expectedCatalogGeneration);
					if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
				} else if ($settings?.tools) {
					selectedToolIds = $settings.tools;
				} else {
					selectedToolIds = selectedToolIds.filter((id) => !id.startsWith('direct_server:'));
				}

				selectedSkillIds = model.info?.meta?.skillIds
					? [
							...new Set(
								model.info.meta.skillIds.filter((id) =>
									($skills ?? []).some((s) => s.id === id && s.is_active)
								)
							)
						]
					: [];
				if (model.info?.meta?.defaultFilterIds) {
					selectedFilterIds = model.info.meta.defaultFilterIds.filter((id) =>
						model.filters?.some((filter) => filter.id === id)
					);
				}
				if (model.info?.meta?.defaultFeatureIds) {
					const defaults = model.info.meta.defaultFeatureIds;
					if (
						model.info.meta.capabilities?.image_generation &&
						$config?.features?.enable_image_generation &&
						hasImageGenerationAccess()
					)
						imageGenerationEnabled = defaults.includes('image_generation');
					if (
						model.info.meta.capabilities?.web_search &&
						$config?.features?.enable_web_search &&
						($user?.role === 'admin' || $user?.permissions?.features?.web_search)
					)
						webSearchEnabled = defaults.includes('web_search');
					if (
						model.info.meta.capabilities?.code_interpreter &&
						$config?.features?.enable_code_interpreter &&
						($user?.role === 'admin' || $user?.permissions?.features?.code_interpreter)
					)
						codeInterpreterEnabled = defaults.includes('code_interpreter');
				}
				imageGenerationEnabled = shouldEnableImageGenerationByDefault(
					model,
					$config?.features?.enable_image_generation ?? false,
					hasImageGenerationAccess()
				);
				imageGenerationUserOverride = null;
				const defaultTerminalId = (model.info?.meta as any)?.terminalId;
				const directTerminalIds = new Set(
					((($settings as any)?.terminalServers ?? []) as any[])
						.map((server) => server?.url)
						.filter((id): id is string => typeof id === 'string')
				);
				if (
					(model.info?.meta?.capabilities as any)?.function_calling !== false &&
					(model.info?.meta?.capabilities as any)?.terminal !== false &&
					typeof defaultTerminalId === 'string' &&
					isTerminalAvailable(defaultTerminalId) &&
					(!directTerminalIds.has(defaultTerminalId) || isDirectToolServersPermitted($user))
				) {
					selectedTerminalId.set(defaultTerminalId);
				}
			}
			if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
			if (modeProfileDraftId.startsWith('draft:')) {
				await applyModeProfileInitialization(externalCatalogRequest, expectedCatalogGeneration);
			}
		})();
		settingDefaultsPromise = {
			generation: expectedCatalogGeneration,
			promise: defaultsPromise
		};
		try {
			await defaultsPromise;
		} finally {
			if (settingDefaultsPromise?.promise === defaultsPromise) {
				settingDefaultsPromise = null;
			}
		}
	};

	const showMessage = async (message, scroll = true, save = true) => {
		const _chatId = JSON.parse(JSON.stringify($chatId));
		let _messageId = JSON.parse(JSON.stringify(message.id));

		let messageChildrenIds = [];
		if (_messageId === null) {
			messageChildrenIds = Object.keys(history.messages).filter(
				(id) => history.messages[id].parentId === null
			);
		} else {
			messageChildrenIds = history.messages[_messageId].childrenIds;
		}

		while (messageChildrenIds.length !== 0) {
			_messageId = messageChildrenIds.at(-1);
			messageChildrenIds = history.messages[_messageId].childrenIds;
		}

		history.currentId = _messageId;

		await tick();

		if (($settings?.scrollOnBranchChange ?? true) && scroll) {
			const messageElement = document.getElementById(`message-${message.id}`);
			if (messageElement) {
				messageElement.scrollIntoView({ behavior: 'smooth', block: 'start' });
			}
		}

		await tick();
		await tick();
		await tick();

		if (save) {
			saveChatHandler(_chatId, history);
		}
	};

	const updateLastReadAt = (id) => {
		$socket?.emit('events:chat', {
			chat_id: id,
			data: { type: 'last_read_at' }
		});
	};

	const terminalEventHandler = (type: string, data: any) => {
		if (type === 'terminal:display_file') {
			if (!data?.path) return;
			displayFileHandler(data.path, { showControls, showFileNavPath });
		} else if (type === 'terminal:write_file' || type === 'terminal:replace_file_content') {
			if (!data?.path) return;
			showFileNavDir.set(data.path);
		} else if (type === 'terminal:run_command') {
			showFileNavDir.set('/');
		}
	};

	const dismissContextCompactionToast = () => {
		if (contextCompactionToastId !== null) {
			toast.dismiss(contextCompactionToastId);
			contextCompactionToastId = null;
		}
	};

	const handleContextCompactionStatus = (status) => {
		if (status?.action !== 'context_compaction') {
			return;
		}

		if (status?.done) {
			if (contextCompactionToastId !== null) {
				if (status?.error) {
					toast.error($i18n.t('Context compaction failed'), {
						id: contextCompactionToastId,
						duration: 3000
					});
				} else {
					toast.success($i18n.t('Context compacted'), {
						id: contextCompactionToastId,
						duration: 1800
					});
				}
				contextCompactionToastId = null;
			}
			return;
		}

		if (contextCompactionToastId === null) {
			contextCompactionToastId = toast.loading($i18n.t('Compacting context'), {
				duration: Infinity
			});
		}
	};

	const chatEventHandler = async (event, cb) => {
		console.log(event);
		const expectedCatalogGeneration = modeProfileCatalogGeneration;

		if (event.chat_id === $chatId) {
			await tick();
			const type = event?.data?.type ?? null;
			if (
				event.chat_id !== $chatId ||
				!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)
			) {
				return;
			}
			if (type === 'chat:reload') {
				await loadChat(event.chat_id, expectedCatalogGeneration);
				return;
			}
			if (type === 'chat:list') {
				return;
			}
			let message = history.messages[event.message_id];

			if (message) {
				const data = event?.data?.data ?? null;
				const applySocketContentEvent = shouldApplySocketContentEvent(message, type);

				if (type === 'status') {
					if (message?.statusHistory) {
						message.statusHistory.push(data);
					} else {
						message.statusHistory = [data];
					}
				} else if (type === 'context_compaction') {
					handleContextCompactionStatus(data);
				} else if (type === 'chat:active') {
					if (!data?.active) {
						taskIds = null;
						if (chatIdProp && !$temporaryChatEnabled && hasPendingAssistantLeaf()) {
							await loadChat(event.chat_id, expectedCatalogGeneration);
						}
						if ($chatId && !$temporaryChatEnabled) {
							updateLastReadAt($chatId);
						}
					}
				} else if (type === 'chat:completion') {
					if (applySocketContentEvent) {
						chatCompletionEventHandler(data, message, event.chat_id, expectedCatalogGeneration);
					}
				} else if (type === 'chat:tasks:cancel') {
					dismissContextCompactionToast();
					if (event.message_id === history.currentId) {
						taskIds = null;
						// Set all response messages to done
						for (const messageId of history.messages[message.parentId].childrenIds) {
							history.messages[messageId].done = true;
						}
						await processNextInQueue(event.chat_id, expectedCatalogGeneration);
					} else {
						message.done = true;
					}
				} else if (type === 'chat:message:delta' || type === 'message') {
					if (applySocketContentEvent) {
						message.content += data.content;
					}
				} else if (type === 'chat:message' || type === 'replace') {
					message.content = data.content;
				} else if (type === 'chat:message:files' || type === 'files') {
					message.files = data.files;
				} else if (type === 'chat:message:tasks') {
					chatTasks = data.tasks;
				} else if (type === 'chat:message:embeds' || type === 'embeds') {
					message.embeds = data.embeds;

					// Auto-scroll to the embed once it's rendered in the DOM
					await tick();
					if (
						event.chat_id !== $chatId ||
						!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)
					) {
						return;
					}
					setTimeout(() => {
						if (
							event.chat_id !== $chatId ||
							!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)
						) {
							return;
						}
						const embedEl = document.getElementById(`${event.message_id}-embeds-container`);
						if (embedEl) {
							embedEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
						}
					}, 100);
				} else if (type === 'chat:message:error') {
					message.error = data.error;
				} else if (type === 'chat:message:follow_ups') {
					message.followUps = data.follow_ups;

					scheduleResponseScrollToBottom();
				} else if (type === 'chat:outlet') {
					// Outlet filter ran on backend — sync in-memory state
					const outletMessages = data.messages ?? [];
					for (const msg of outletMessages) {
						if (msg?.id && history.messages[msg.id]) {
							const existing = history.messages[msg.id];
							if (existing.content !== msg.content) {
								history.messages[msg.id] = {
									...existing,
									originalContent: existing.content,
									...msg
								};
							}
						}
					}
					history = history;
					return; // Patches history.messages directly; skip the trailing write-back.
				} else if (type === 'chat:message:favorite') {
					// Update message favorite status
					message.favorite = data.favorite;
				} else if (type === 'chat:title') {
					chatTitle.set(data);
					if (embedded && event.chat_id) {
						await onEmbeddedChatTitle?.(event.chat_id, data);
					}
					if (
						event.chat_id !== $chatId ||
						!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)
					) {
						return;
					}
					await refreshChatList(localStorage.token);
				} else if (type === 'chat:tags') {
					const loadedTaggedChat = await getChatById(localStorage.token, event.chat_id);
					if (
						event.chat_id !== $chatId ||
						!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)
					) {
						return;
					}
					const loadedAllTags = await getAllTags(localStorage.token);
					if (
						event.chat_id !== $chatId ||
						!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)
					) {
						return;
					}
					chat = loadedTaggedChat;
					allTags.set(loadedAllTags);
				} else if (type === 'source' || type === 'citation') {
					if (data?.type === 'code_execution') {
						// Code execution; update existing code execution by ID, or add new one.
						if (!message?.code_executions) {
							message.code_executions = [];
						}

						const existingCodeExecutionIndex = message.code_executions.findIndex(
							(execution) => execution.id === data.id
						);

						if (existingCodeExecutionIndex !== -1) {
							message.code_executions[existingCodeExecutionIndex] = data;
						} else {
							message.code_executions.push(data);
						}

						message.code_executions = message.code_executions;
					} else {
						// Regular source.
						if (message?.sources) {
							message.sources.push(data);
						} else {
							message.sources = [data];
						}
					}
				} else if (type === 'notification') {
					const toastType = data?.type ?? 'info';
					const toastContent = data?.content ?? '';

					if (toastType === 'success') {
						toast.success(toastContent);
					} else if (toastType === 'error') {
						toast.error(toastContent);
					} else if (toastType === 'warning') {
						toast.warning(toastContent);
					} else {
						toast.info(toastContent);
					}
				} else if (type === 'confirmation') {
					eventCallback = cb;

					eventConfirmationInput = false;
					showEventConfirmation = true;
					eventConfirmationInputOptions = [];

					eventConfirmationTitle = data.title;
					eventConfirmationMessage = data.message;
				} else if (type === 'execute') {
					eventCallback = cb;

					try {
						// Use Function constructor to evaluate code in a safer way
						const asyncFunction = new Function(`return (async () => { ${data.code} })()`);
						const result = await asyncFunction(); // Await the result of the async function

						if (
							event.chat_id === $chatId &&
							isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration) &&
							cb
						) {
							cb(result);
						}
					} catch (error) {
						console.error('Error executing code:', error);
					}
				} else if (type === 'input') {
					eventCallback = cb;

					eventConfirmationInput = true;
					showEventConfirmation = true;

					eventConfirmationTitle = data.title;
					eventConfirmationMessage = data.message;
					eventConfirmationInputPlaceholder = data.placeholder;
					eventConfirmationInputValue = data?.value ?? '';
					eventConfirmationInputType = data?.input?.type ?? data?.type ?? '';
					eventConfirmationInputOptions = data?.input?.options ?? data?.options ?? [];
				} else if (type.startsWith('terminal:')) {
					terminalEventHandler(type, data);
				} else {
					console.log('Unknown message type', data);
				}

				if (
					event.chat_id !== $chatId ||
					!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)
				) {
					return;
				}
				history.messages[event.message_id] = message;
			}
		} else {
			// Non-active chat completion: queue stays in the global store.
			// navigateHandler will process it when the user returns to that chat.
		}
	};

	const onMessageHandler = async (event: {
		origin: string;
		source: unknown;
		data: { type: string; text: string };
	}) => {
		const isSameOrigin = event.origin === window.origin;
		const type = event.data?.type;

		// Prompt-driving types are trusted only same-origin, from our own embed iframes
		// (opaque srcdoc origin, submission still confirmed below) or via explicit opt-in.
		const promptTypes = ['input:prompt', 'input:prompt:submit', 'action:submit'];
		const isOwnEmbed = isEmbedWindow(event.source);
		const isTrusted =
			isSameOrigin || isOwnEmbed || ($settings?.iframeSandboxAllowSameOrigin ?? false);

		// Non-prompt message types are always restricted to same-origin only.
		if (!isSameOrigin && !promptTypes.includes(type)) {
			return;
		}

		// Prompt types from an untrusted cross-origin source are silently dropped.
		if (promptTypes.includes(type) && !isTrusted) {
			return;
		}

		if (type === 'action:submit') {
			console.debug(event.data.text);

			if (prompt !== '') {
				if (isSameOrigin) {
					await tick();
					submitHandler(prompt);
				} else {
					eventConfirmationInput = false;
					eventConfirmationTitle = $i18n.t('Confirm Prompt from Embed');
					eventConfirmationMessage = prompt;
					eventCallback = async (confirmed: boolean) => {
						if (confirmed) {
							await tick();
							submitHandler(prompt);
						}
					};
					showEventConfirmation = true;
				}
			}
		}

		if (type === 'input:prompt') {
			console.debug(event.data.text);

			const inputElement = document.getElementById('chat-input');

			if (inputElement) {
				messageInput?.setText(event.data.text);
				inputElement.focus();
			}
		}

		if (type === 'input:prompt:submit') {
			console.debug(event.data.text);

			if (event.data.text !== '') {
				if (isSameOrigin) {
					await tick();
					submitHandler(event.data.text);
				} else {
					eventConfirmationInput = false;
					eventConfirmationTitle = $i18n.t('Confirm Prompt from Embed');
					eventConfirmationMessage = event.data.text;
					eventCallback = async (confirmed: boolean) => {
						if (confirmed) {
							await tick();
							submitHandler(event.data.text);
						}
					};
					showEventConfirmation = true;
				}
			}
		}
	};

	const savedModelIds = async () => {
		if (
			$selectedFolder &&
			selectedModels.filter((modelId) => modelId !== '').length > 0 &&
			!equal($selectedFolder?.data?.model_ids, selectedModels)
		) {
			const res = await updateFolderById(localStorage.token, $selectedFolder.id, {
				data: {
					model_ids: selectedModels
				}
			});
		}
	};

	$: if (selectedModels !== null) {
		savedModelIds();
	}

	const stopAudio = () => {
		try {
			speechSynthesis.cancel();
			$audioQueue?.stop();
		} catch {}
	};

	const hasPendingAssistantLeaf = () =>
		Object.values(history.messages).some(
			(message) =>
				message?.role === 'assistant' && !message.done && (message.childrenIds?.length ?? 0) === 0
		);

	const handleSocketConnect = async () => {
		// Gate on $chatId, not chatIdProp: chats started from the home page keep an empty chatIdProp
		if (!$chatId || $temporaryChatEnabled) {
			return;
		}

		if (!hasPendingAssistantLeaf()) {
			return;
		}
		const targetChatId = $chatId;
		const expectedCatalogGeneration = modeProfileCatalogGeneration;

		const pendingTaskIds = await getTaskIdsByChatId(localStorage.token, targetChatId)
			.then((res) => res?.task_ids ?? [])
			.catch(() => null);

		if (
			pendingTaskIds?.length === 0 &&
			$chatId === targetChatId &&
			isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)
		) {
			await loadChat(targetChatId, expectedCatalogGeneration);
		}
	};

	onMount(() => {
		loading = true;
		console.log('mounted');
		window.addEventListener('message', onMessageHandler);
		$socket?.on('events', chatEventHandler);
		$socket?.on('connect', handleSocketConnect);

		$audioQueue?.destroy();

		const audioQueueInstance = new AudioQueue(document.getElementById('audioElement'));
		audioQueue.set(audioQueueInstance);

		// Restore direct terminal enabled states based on persisted selectedTerminalId.
		// A revoked direct-server permission must not reactivate a restored direct terminal.
		if ($settings?.terminalServers?.length) {
			settings.set({
				...$settings,
				terminalServers: ($settings.terminalServers ?? []).map((s) => ({
					...s,
					enabled:
						isDirectToolServersPermitted($user) &&
						$selectedTerminalId !== null &&
						s.url === $selectedTerminalId
				}))
			});
		}

		// Clear stale selectedTerminalId if the referenced terminal no longer exists
		const mountedExternalCatalogRequest = getModeProfileExternalCatalogRequest();
		if (
			modeProfileExternalCatalogCache.snapshot(mountedExternalCatalogRequest.fingerprint).catalog &&
			$selectedTerminalId &&
			!isTerminalAvailable($selectedTerminalId)
		) {
			clearSelectedTerminal();
		}

		const pageSubscribe = page.subscribe(async (p) => {
			if (p.url.pathname === '/' || p.url.pathname.startsWith('/folders/')) {
				await tick();
				initNewChat();
			}

			stopAudio();
		});

		const showControlsSubscribe = showControls.subscribe(async (value) => {
			await tick();
			if (controlPane && !$mobile) {
				try {
					if (value) {
						controlPaneComponent?.openPane();
					} else {
						controlPane.collapse();
					}
				} catch (e) {
					// ignore
				}
			}

			if (!value) {
				showCallOverlay.set(false);
				showArtifacts.set(false);
				showEmbeds.set(false);
			}
		});

		const selectedFolderSubscribe = selectedFolder.subscribe(async (folder) => {
			await tick();
			if (folder?.data?.model_ids && !equal(selectedModels, folder.data.model_ids)) {
				selectedModels = folder.data.model_ids;

				console.log('Set selectedModels from folder data:', selectedModels);
			}
		});

		const storageChatInput = sessionStorage.getItem(
			`chat-input${chatIdProp ? `-${chatIdProp}` : ''}`
		);
		const restoredDraft = parseConversationModeDraft(storageChatInput);
		if (storageChatInput && !restoredDraft) {
			sessionStorage.removeItem(`chat-input${chatIdProp ? `-${chatIdProp}` : ''}`);
		}

		const init = async () => {
			if (!chatIdProp) {
				await tick();
			}

			if (restoredDraft && !chatIdProp) {
				prompt = '';
				messageInput?.setText('');

				files = [];
				reasoningEffort = 'medium';

				if (!$temporaryChatEnabled) {
					prompt = restoredDraft.prompt;
					messageInput?.setText(restoredDraft.prompt);
					files = restoredDraft.files;
					reasoningEffort = normalizeReasoningEffort(
						restoredDraft.reasoningEffort ?? restoredDraft.reasoningDepth
					);
					if (typeof restoredDraft.conversationMode === 'string') {
						conversationMode = normalizeConversationMode(restoredDraft.conversationMode);
					}
				}
			}

			const chatInput = document.getElementById('chat-input');
			chatInput?.focus();
		};
		init();

		return () => {
			invalidateModeProfileCatalogGeneration();
			try {
				clearTimeout(saveControlsTimer);
				saveControls();
				if (chatIdProp && !$temporaryChatEnabled) {
					updateLastReadAt(chatIdProp);
				}
				pageSubscribe();
				showControlsSubscribe();
				selectedFolderSubscribe();

				// Clear the selected chat when leaving the chat surface (e.g. navigating
				// to the admin panel), otherwise the previously-viewed chat stays selected
				// in the sidebar and deleting/archiving it wrongly navigates away.
				chatId.set('');
				chatTitle.set('');

				window.removeEventListener('message', onMessageHandler);
				$socket?.off('events', chatEventHandler);
				$socket?.off('connect', handleSocketConnect);
				dismissContextCompactionToast();
				audioQueueInstance?.destroy();
				audioQueue.set(null);
			} catch (e) {
				console.error(e);
			}
		};
	});

	// File upload functions

	const uploadGoogleDriveFile = async (fileData) => {
		console.log('Starting uploadGoogleDriveFile with:', {
			id: fileData.id,
			name: fileData.name,
			url: fileData.url,
			headers: {
				Authorization: `Bearer ${token}`
			}
		});

		// Validate input
		if (!fileData?.id || !fileData?.name || !fileData?.url || !fileData?.headers?.Authorization) {
			throw new Error('Invalid file data provided');
		}

		const tempItemId = uuidv4();
		const fileItem = {
			type: 'file',
			file: '',
			id: null,
			url: fileData.url,
			name: fileData.name,
			collection_name: '',
			status: 'uploading',
			error: '',
			itemId: tempItemId,
			size: 0
		};

		try {
			files = [...files, fileItem];
			console.log('Processing web file with URL:', fileData.url);

			// Configure fetch options with proper headers
			const fetchOptions = {
				headers: {
					Authorization: fileData.headers.Authorization,
					Accept: '*/*'
				},
				method: 'GET'
			};

			// Attempt to fetch the file
			console.log('Fetching file content from Google Drive...');
			const fileResponse = await fetch(fileData.url, fetchOptions);

			if (!fileResponse.ok) {
				const errorText = await fileResponse.text();
				throw new Error(`Failed to fetch file (${fileResponse.status}): ${errorText}`);
			}

			// Get content type from response
			const contentType = fileResponse.headers.get('content-type') || 'application/octet-stream';
			console.log('Response received with content-type:', contentType);

			// Convert response to blob
			console.log('Converting response to blob...');
			const fileBlob = await fileResponse.blob();

			if (fileBlob.size === 0) {
				throw new Error('Retrieved file is empty');
			}

			console.log('Blob created:', {
				size: fileBlob.size,
				type: fileBlob.type || contentType
			});

			// Create File object with proper MIME type
			const file = new File([fileBlob], fileData.name, {
				type: fileBlob.type || contentType
			});

			console.log('File object created:', {
				name: file.name,
				size: file.size,
				type: file.type
			});

			if (file.size === 0) {
				throw new Error('Created file is empty');
			}

			// If the file is an audio file, provide the language for STT.
			let metadata: Record<string, string> = {
				upload_context: 'chat'
			};
			if (
				(file.type.startsWith('audio/') || file.type.startsWith('video/')) &&
				$settings?.audio?.stt?.language
			) {
				metadata = {
					...metadata,
					language: $settings?.audio?.stt?.language
				};
			}

			// Upload file to server
			console.log('Uploading file to server...');
			const uploadedFile = await uploadFile(localStorage.token, file, metadata);

			if (!uploadedFile) {
				throw new Error('Server returned null response for file upload');
			}

			console.log('File uploaded successfully:', uploadedFile);

			// Update file item with upload results
			fileItem.status = 'uploaded';
			fileItem.file = uploadedFile;
			fileItem.id = uploadedFile.id;
			fileItem.size = file.size;
			fileItem.collection_name = uploadedFile?.meta?.collection_name;
			fileItem.url = `${uploadedFile.id}`;

			files = files;
			toast.success($i18n.t('File uploaded successfully'));
		} catch (e) {
			console.error('Error uploading file:', e);
			files = files.filter((f) => f.itemId !== tempItemId);
			toast.error(
				$i18n.t('Error uploading file: {{error}}', {
					error: e.message || 'Unknown error'
				})
			);
		}
	};

	const uploadWeb = async (urls) => {
		if ($user?.role !== 'admin' && !($user?.permissions?.chat?.web_upload ?? true)) {
			toast.error($i18n.t('You do not have permission to upload web content.'));
			return;
		}

		if (!Array.isArray(urls)) {
			urls = [urls];
		}

		// Create file items first
		const fileItems = urls.map((url) => ({
			type: 'text',
			name: url,
			collection_name: '',
			status: 'uploading',
			context: 'full',
			url,
			error: ''
		}));

		// Display all items at once
		files = [...files, ...fileItems];

		for (const fileItem of fileItems) {
			try {
				const res = isYoutubeUrl(fileItem.url)
					? await processYoutubeVideo(localStorage.token, fileItem.url)
					: await processWeb(localStorage.token, '', fileItem.url);

				if (res) {
					fileItem.status = 'uploaded';
					fileItem.collection_name = res.collection_name;
					fileItem.file = {
						...res.file,
						...fileItem.file
					};
				}

				files = [...files];
			} catch (e) {
				files = files.filter((f) => f.name !== url);
				toast.error(`${e}`);
			}
		}
	};

	const onUpload = async (event) => {
		const { type, data } = event;

		if (type === 'google-drive') {
			await uploadGoogleDriveFile(data);
		} else if (type === 'web') {
			await uploadWeb(data);
		}
	};

	const onHistoryChange = (history) => {
		if (history) {
			clearTimeout(contentsRAF);
			contentsRAF = setTimeout(() => {
				getContents();
				contentsRAF = null;
			}, 0);
		} else {
			artifactContents.set([]);
		}
	};

	$: onHistoryChange(history);

	const dispatchCallOverlayAudio = (message, final = false) => {
		if (!$showCallOverlay) {
			return;
		}

		const messageContentParts = getMessageContentParts(
			getOutputText(message?.output) || removeAllDetails(message?.content ?? ''),
			$config?.audio?.tts?.split_on ?? 'punctuation'
		);
		if (!final) {
			messageContentParts.pop();
		}

		const nextContentPart = messageContentParts.at(-1) ?? '';
		if (!nextContentPart || (!final && nextContentPart === message.lastSentence)) {
			return;
		}

		if (!final) {
			message.lastSentence = nextContentPart;
		}

		eventTarget.dispatchEvent(
			new CustomEvent('chat', {
				detail: {
					id: message.id,
					content: nextContentPart
				}
			})
		);
	};

	const getContents = () => {
		const messages = history ? createMessagesList(history, history.currentId) : [];
		let contents = [];
		messages.forEach((message) => {
			if (message?.role !== 'user') {
				const messageContent =
					getOutputText(message?.output) || removeAllDetails(message?.content ?? '');
				if (!messageContent.trim()) {
					return;
				}

				const { codeBlocks: codeBlocks, htmlGroups: htmlGroups } =
					getCodeBlockContents(messageContent);

				if (htmlGroups && htmlGroups.length > 0) {
					htmlGroups.forEach((group) => {
						const renderedContent = `
                        <!DOCTYPE html>
                        <html lang="en">
                        <head>
                            <meta charset="UTF-8">
                            <meta name="viewport" content="width=device-width, initial-scale=1.0">
							<${''}style>
								body {
									background-color: white; /* Ensure the iframe has a white background */
								}

								${group.css}
							</${''}style>
                        </head>
                        <body>
                            ${group.html}

							<${''}script>
                            	${group.js}
							</${''}script>
                        </body>
                        </html>
                    `;
						contents = [...contents, { type: 'iframe', content: renderedContent }];
					});
				} else {
					// Check for SVG content
					for (const block of codeBlocks) {
						if (block.lang === 'svg' || (block.lang === 'xml' && block.code.includes('<svg'))) {
							contents = [...contents, { type: 'svg', content: block.code }];
						}
					}
				}
			}
		});

		artifactContents.set(contents);
	};

	//////////////////////////
	// Web functions
	//////////////////////////

	const initNewChat = async (preselectedMode: ConversationMode | null = null) => {
		console.log('initNewChat');
		loading = true;
		resetWebSearchConfirmation();
		const requestedMode =
			preselectedMode ?? normalizeConversationMode($page.url.searchParams.get('mode'));
		conversationMode = requestedMode;
		const restoredRootDraft = beginModeProfileDraft();
		const expectedCatalogGeneration = modeProfileCatalogGeneration;
		const restoredRootCapabilitySnapshot = getConversationModeDraftCapabilitySnapshotForMode(
			restoredRootDraft,
			conversationMode,
			{ existingChat: false }
		);
		pendingConversationMode = null;
		chat = null;

		// Mark the outgoing chat as read before resetting; in-place created chats
		// keep chatIdProp undefined, so navigateHandler never marks them read.
		if ($chatId && !$temporaryChatEnabled) {
			updateLastReadAt($chatId);
		}

		if ($user?.role !== 'admin' && $user?.permissions?.chat?.temporary_enforced) {
			await temporaryChatEnabled.set(true);
			if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
		}

		if ($settings?.temporaryChatByDefault ?? false) {
			if ($temporaryChatEnabled === false) {
				await temporaryChatEnabled.set(true);
				if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
			} else if ($temporaryChatEnabled === null) {
				// if set to null set to false; refer to temp chat toggle click handler
				await temporaryChatEnabled.set(false);
				if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
			}
		}

		if ($user?.role !== 'admin' && !$user?.permissions?.chat?.temporary) {
			await temporaryChatEnabled.set(false);
			if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
		}

		const availableModels = $models
			.filter((m) => !(m?.info?.meta?.hidden ?? false))
			.map((m) => m.id);

		const defaultModels = $config?.default_models ? $config?.default_models.split(',') : [];

		if ($page.url.searchParams.get('models') || $page.url.searchParams.get('model')) {
			const urlModels = (
				$page.url.searchParams.get('models') ||
				$page.url.searchParams.get('model') ||
				''
			)?.split(',');

			if (urlModels.length === 1) {
				if (!$models.find((m) => m.id === urlModels[0])) {
					// Model not found; open model selector and prefill
					const modelSelectorButton = document.getElementById('model-selector-0-button');
					if (modelSelectorButton) {
						modelSelectorButton.click();
						await tick();
						if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;

						const modelSelectorInput = document.getElementById('model-search-input');
						if (modelSelectorInput) {
							modelSelectorInput.focus();
							modelSelectorInput.value = urlModels[0];
							modelSelectorInput.dispatchEvent(new Event('input'));
						}
					}
				} else {
					// Model found; set it as selected
					selectedModels = urlModels;
				}
			} else {
				// Multiple models; set as selected
				selectedModels = urlModels;
			}

			// Unavailable models filtering
			selectedModels = selectedModels.filter((modelId) =>
				$models.map((m) => m.id).includes(modelId)
			);
		} else {
			if ($selectedFolder?.data?.model_ids) {
				// Set from folder model IDs
				selectedModels = $selectedFolder?.data?.model_ids;
			} else {
				if (sessionStorage.selectedModels) {
					// Set from session storage (temporary selection)
					selectedModels = JSON.parse(sessionStorage.selectedModels);
					sessionStorage.removeItem('selectedModels');
				} else {
					if ($settings?.models) {
						// Set from user settings
						selectedModels = $settings?.models;
					} else if (defaultModels && defaultModels.length > 0) {
						// Set from default models
						selectedModels = defaultModels;
					}
				}
			}

			// Unavailable & hidden models filtering
			selectedModels = selectedModels.filter((modelId) => availableModels.includes(modelId));
		}

		// Ensure at least one model is selected
		if (selectedModels.length === 0 || (selectedModels.length === 1 && selectedModels[0] === '')) {
			if (availableModels.length > 0) {
				if (defaultModels && defaultModels.length > 0) {
					selectedModels = defaultModels.filter((modelId) => availableModels.includes(modelId));
				}

				if (
					selectedModels.length === 0 ||
					(selectedModels.length === 1 && selectedModels[0] === '')
				) {
					// Only fall back to first available model if default models didn't resolve
					selectedModels = [availableModels?.at(0) ?? ''];
				}
			} else {
				selectedModels = [''];
			}
		}

		if ($mobile) {
			await showControls.set(false);
			if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
		}
		await showCallOverlay.set(false);
		if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
		await showArtifacts.set(false);
		if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;

		if (!embedded && $page.url.pathname.includes('/c/')) {
			window.history.replaceState(history.state, '', `/`);
		}

		autoScroll = true;

		await resetInput({ initialize: restoredRootCapabilitySnapshot === null });
		if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
		if (restoredRootCapabilitySnapshot) {
			await restoreModeProfileCapabilitySnapshot(
				restoredRootCapabilitySnapshot,
				expectedCatalogGeneration
			);
		} else {
			await restoreLegacyModeProfileDraftCapabilities(
				isConversationModeDraftCompatible(restoredRootDraft, conversationMode)
					? restoredRootDraft
					: null,
				{
					expectedCatalogGeneration
				}
			);
		}
		if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
		await chatId.set('');
		if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
		await chatTitle.set('');
		if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;

		history = {
			messages: {},
			currentId: null
		};

		chatFiles = [];
		params = {};
		chatVariables = {};
		taskIds = null;
		chatTasks = [];

		if ($page.url.searchParams.get('youtube')) {
			await uploadWeb(`https://www.youtube.com/watch?v=${$page.url.searchParams.get('youtube')}`);
			if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
		}

		if ($page.url.searchParams.get('load-url')) {
			await uploadWeb($page.url.searchParams.get('load-url'));
			if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
		}

		if ($page.url.searchParams.get('web-search') === 'true') {
			webSearchEnabled = true;
		}

		if ($page.url.searchParams.get('image-generation') === 'true') {
			imageGenerationEnabled = true;
			imageGenerationUserOverride = true;
		}

		if ($page.url.searchParams.get('code-interpreter') === 'true') {
			codeInterpreterEnabled = true;
		}

		if ($page.url.searchParams.get('tools')) {
			selectedToolIds = $page.url.searchParams
				.get('tools')
				?.split(',')
				.map((id) => id.trim())
				.filter((id) => id);
		} else if ($page.url.searchParams.get('tool-ids')) {
			selectedToolIds = $page.url.searchParams
				.get('tool-ids')
				?.split(',')
				.map((id) => id.trim())
				.filter((id) => id);
		}

		// Restore tool selection after OAuth redirect
		const pendingToolId = sessionStorage.getItem('pendingOAuthToolId');
		if (pendingToolId) {
			sessionStorage.removeItem('pendingOAuthToolId');
			if (!selectedToolIds.includes(pendingToolId)) {
				selectedToolIds = [...selectedToolIds, pendingToolId];
			}
		}

		await finalizeModeProfileCapabilitySnapshot(expectedCatalogGeneration);
		if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
		loading = false;
		await tick();
		if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;

		if ($page.url.searchParams.get('call') === 'true') {
			showCallOverlay.set(true);
			showControls.set(true);
		}

		// Consume one-shot desktop event (e.g. Spotlight query, call shortcut)
		if ($desktopEvent) {
			const event = $desktopEvent;
			desktopEvent.set(null);

			if (event.type === 'call') {
				// Defer to next macrotask so the call overlay isn't clobbered by
				// showControlsSubscribe's initial callback (value=false → set(false))
				// which runs as a pending microtask after this function.
				setTimeout(() => {
					if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
					showCallOverlay.set(true);
					showControls.set(true);
				}, 0);
			} else if (event.type === 'query') {
				const query = event.data?.query;
				const eventFiles = event.data?.files;

				// Attach screenshot images from desktop (e.g. Spotlight region capture)
				if (eventFiles?.length) {
					for (const ef of eventFiles) {
						files = [
							...files,
							{
								type: 'image',
								url: ef.dataUrl,
								name: ef.name
							}
						];
					}
				}

				if (query || eventFiles?.length) {
					if (query) {
						messageInput?.setText(query);
					}
					await tick();
					if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
					submitHandler(query || '');
				}
			}
		} else if ($page.url.searchParams.get('q')) {
			const q = $page.url.searchParams.get('q') ?? '';
			messageInput?.setText(q);

			if (q) {
				if (($page.url.searchParams.get('submit') ?? 'true') === 'true') {
					await tick();
					if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
					submitHandler(q);
				}
			}
		}

		selectedModels = selectedModels.map((modelId) =>
			$models.map((m) => m.id).includes(modelId) ? modelId : ''
		);

		const chatInput = document.getElementById('chat-input');
		setTimeout(() => {
			if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
			chatInput?.focus();
		}, 0);
	};

	const loadChat = async (
		targetChatId = chatIdProp,
		expectedCatalogGeneration = modeProfileCatalogGeneration
	) => {
		if (!targetChatId || !isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) {
			return false;
		}
		chatId.set(targetChatId);

		if ($temporaryChatEnabled) {
			noteChatDebug('loadChat disabling temporary chat');
			temporaryChatEnabled.set(false);
		}

		const loadedChat = await getChatById(localStorage.token, targetChatId).catch((error) => {
			console.error('[load chat]', error);
			return null;
		});
		if (
			$chatId !== targetChatId ||
			!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)
		) {
			return false;
		}
		if (!loadedChat) {
			await goto('/');
			return null;
		}

		const loadedTags = await getTagsById(localStorage.token, targetChatId).catch(() => []);
		if (
			$chatId !== targetChatId ||
			!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)
		) {
			return false;
		}
		chat = loadedChat;
		tags = loadedTags;

		const chatContent = loadedChat.chat;

		if (chatContent) {
			console.log(chatContent);
			conversationMode = normalizeConversationMode(chatContent?.mode);
			bindCanonicalModeProfileRevision(loadedChat?.mode_profile_revision_id);

			selectedModels =
				(chatContent?.models ?? undefined) !== undefined
					? chatContent.models
					: [chatContent.models ?? ''];

			if (!($user?.role === 'admin' || ($user?.permissions?.chat?.multiple_models ?? true))) {
				selectedModels = selectedModels.length > 0 ? [selectedModels[0]] : [''];
			}

			oldSelectedModelIds = structuredClone(selectedModels);

			const loadedHistory =
				(chatContent?.history ?? undefined) !== undefined
					? chatContent.history
					: convertMessagesToHistory(chatContent.messages);

			// Sanitize history: repair orphaned references and structurally-malformed
			// nodes from failed regenerations (#24424, #24157, #20474)
			sanitizeHistory(loadedHistory);

			chatTitle.set(chatContent.title);

			params = structuredClone(chatContent?.params ?? {});
			chatFiles = structuredClone(chatContent?.files ?? []);

			// Load tasks from chat-level DB field
			chatTasks = loadedChat?.tasks ?? [];

			autoScroll = true;
			await tick();
			if (
				$chatId !== targetChatId ||
				!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)
			) {
				return false;
			}

			// Reconcile active tasks with message state:
			// If the response is already done, remaining tasks are just background
			// work (follow-ups, title gen) that shouldn't block the input.
			const activeTaskIds = taskIds;
			const pendingTaskIds = await getTaskIdsByChatId(localStorage.token, targetChatId)
				.then((res) => res?.task_ids ?? [])
				.catch(() => []);
			if (
				$chatId !== targetChatId ||
				!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)
			) {
				return false;
			}
			if (taskIds !== activeTaskIds) {
				return;
			}
			const loadedHistoryState = prepareLoadedChatHistory(history, loadedHistory, pendingTaskIds);
			history = loadedHistoryState.history;
			taskIds = loadedHistoryState.taskIds;

			await tick();
			if (
				$chatId !== targetChatId ||
				!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)
			) {
				return false;
			}

			return true;
		} else {
			return null;
		}
		console.warn('[note-chat] no chat returned from getChatById', {
			chatIdProp,
			activeChatId: $chatId
		});
	};

	const scrollToBottom = async (behavior = 'auto') => {
		await tick();
		if (messagesContainerElement) {
			messagesContainerElement.scrollTo({
				top: messagesContainerElement.scrollHeight,
				behavior
			});

			// content-visibility: auto causes the initial scrollHeight to be based on
			// estimated sizes (contain-intrinsic-size). After we scroll, previously
			// off-screen messages become visible and the browser resolves their actual
			// heights, which shifts scrollHeight. Re-layouts can cascade across frames
			// (new sizes reveal more content, triggering further size resolution), so
			// we re-scroll across two animation frames to land at the true bottom.
			requestAnimationFrame(() => {
				if (messagesContainerElement) {
					messagesContainerElement.scrollTo({
						top: messagesContainerElement.scrollHeight,
						behavior
					});
					requestAnimationFrame(() => {
						if (messagesContainerElement) {
							messagesContainerElement.scrollTo({
								top: messagesContainerElement.scrollHeight,
								behavior
							});
						}
					});
				}
			});
		}
	};

	const scrollToTop = async () => {
		await messagesRef?.scrollToTop();
	};

	const shouldAutoScrollResponse = () =>
		autoScroll && ($settings?.scrollOnResponseGeneration ?? true);
	let scrollRAF: number | null = null;
	let contentsRAF = null;
	const scheduleResponseScrollToBottom = () => {
		if (!shouldAutoScrollResponse() || scrollRAF !== null) return;
		scrollRAF = requestAnimationFrame(async () => {
			scrollRAF = null;
			await scrollToBottom();
		});
	};

	let processingQueueChats = new Set<string>();

	const processNextInQueue = async (
		targetChatId: string,
		expectedCatalogGeneration = modeProfileCatalogGeneration
	) => {
		if (
			$chatId !== targetChatId ||
			!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)
		) {
			return;
		}
		if (processingQueueChats.has(targetChatId)) return;

		const queue = $chatRequestQueues[targetChatId];
		if (!queue || queue.length === 0) return;

		processingQueueChats.add(targetChatId);
		try {
			const combinedPrompt = queue.map((m) => m.prompt).join('\n\n');
			const combinedFiles = queue.flatMap((m) => m.files);

			chatRequestQueues.update((q) => {
				const { [targetChatId]: _, ...rest } = q;
				return rest;
			});

			await submitPrompt(combinedPrompt, combinedFiles);
		} finally {
			processingQueueChats.delete(targetChatId);
		}
	};

	const chatCompletedHandler = async (
		_chatId,
		modelId,
		responseMessageId,
		expectedCatalogGeneration = modeProfileCatalogGeneration
	) => {
		// Backend handles outlet filters and persistence inline.
		// Just refresh the sidebar chat list.
		const targetChatId = _chatId;
		if (
			$chatId === targetChatId &&
			isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration) &&
			!$temporaryChatEnabled
		) {
			await refreshChatList(localStorage.token);
		}
	};

	const chatActionHandler = async (_chatId, actionId, modelId, responseMessageId, event = null) => {
		const targetChatId = _chatId;
		const expectedCatalogGeneration = modeProfileCatalogGeneration;
		if (
			$chatId !== targetChatId ||
			!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)
		) {
			return;
		}
		const messages = createMessagesList(history, responseMessageId);

		const res = await chatAction(localStorage.token, actionId, {
			model: modelId,
			messages: messages.map((m) => ({
				id: m.id,
				role: m.role,
				content: getOutputText(m.output) || m.content,
				info: m.info ? m.info : undefined,
				timestamp: m.timestamp,
				...(m.sources ? { sources: m.sources } : {})
			})),
			...(event ? { event: event } : {}),
			model_item: $models.find((m) => m.id === modelId),
			chat_id: targetChatId,
			session_id: $socket?.id,
			id: responseMessageId
		}).catch((error) => {
			if (
				$chatId === targetChatId &&
				isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)
			) {
				toast.error(`${error}`);
				messages.at(-1).error = { content: error };
			}
			return null;
		});
		if (
			$chatId !== targetChatId ||
			!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)
		) {
			return;
		}

		if (res !== null && res.messages) {
			// Update chat history with the new messages
			for (const message of res.messages) {
				history.messages[message.id] = {
					...history.messages[message.id],
					...(history.messages[message.id].content !== message.content
						? { originalContent: history.messages[message.id].content }
						: {}),
					...message
				};
			}
		}

		if ($chatId === targetChatId) {
			if (!$temporaryChatEnabled) {
				const savedChat = await updateChatById(localStorage.token, targetChatId, {
					models: selectedModels,
					messages: messages,
					history: history,
					params: params,
					files: chatFiles
				});
				if (
					$chatId !== targetChatId ||
					!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)
				) {
					return;
				}
				chat = savedChat;

				await refreshChatList(localStorage.token);
			}
		}
	};

	const getChatEventEmitter = async (modelId: string, chatId: string = '') => {
		return setInterval(() => {
			$socket?.emit('usage', {
				action: 'chat',
				model: modelId,
				chat_id: chatId
			});
		}, 1000);
	};

	const createMessagePair = async (userPrompt) => {
		if (selectedModels.length === 0) {
			toast.error($i18n.t('Model not selected'));
		} else {
			const modelId = selectedModels[0];
			const model = $models.filter((m) => m.id === modelId).at(0);

			if (!model) {
				toast.error($i18n.t('Model not found'));
				return;
			}

			await runAcceptedSubmitDraftCriticalSection(async () => {
				await messageInput?.setText('');
				prompt = '';
			});

			const messages = createMessagesList(history, history.currentId);
			const parentMessage = messages.length !== 0 ? messages.at(-1) : null;

			const userMessageId = uuidv4();
			const responseMessageId = uuidv4();

			const userMessage = {
				id: userMessageId,
				parentId: parentMessage ? parentMessage.id : null,
				childrenIds: [responseMessageId],
				role: 'user',
				content: userPrompt ? userPrompt : `[PROMPT] ${userMessageId}`,
				timestamp: Math.floor(Date.now() / 1000)
			};

			const responseMessage = {
				id: responseMessageId,
				parentId: userMessageId,
				childrenIds: [],
				role: 'assistant',
				content: `[RESPONSE] ${responseMessageId}`,
				done: true,

				model: modelId,
				modelName: model.name ?? model.id,
				modelIdx: 0,
				timestamp: Math.floor(Date.now() / 1000)
			};

			if (parentMessage) {
				parentMessage.childrenIds.push(userMessageId);
				history.messages[parentMessage.id] = parentMessage;
			}
			history.messages[userMessageId] = userMessage;
			history.messages[responseMessageId] = responseMessage;

			history.currentId = responseMessageId;

			await tick();

			if (autoScroll) {
				scrollToBottom();
			}

			if (messages.length === 0) {
				await initChatHandler(history);
			} else {
				await saveChatHandler($chatId, history);
			}
		}
	};

	const addMessages = async ({ modelId, parentId, messages }) => {
		const model = $models.filter((m) => m.id === modelId).at(0);

		let parentMessage = history.messages[parentId];
		let currentParentId = parentMessage ? parentMessage.id : null;
		for (const message of messages) {
			let messageId = uuidv4();

			if (message.role === 'user') {
				const userMessage = {
					id: messageId,
					parentId: currentParentId,
					childrenIds: [],
					timestamp: Math.floor(Date.now() / 1000),
					...message
				};

				if (parentMessage) {
					parentMessage.childrenIds.push(messageId);
					history.messages[parentMessage.id] = parentMessage;
				}

				history.messages[messageId] = userMessage;
				parentMessage = userMessage;
				currentParentId = messageId;
			} else {
				const responseMessage = {
					id: messageId,
					parentId: currentParentId,
					childrenIds: [],
					done: true,
					model: model.id,
					modelName: model.name ?? model.id,
					modelIdx: 0,
					timestamp: Math.floor(Date.now() / 1000),
					...message
				};

				if (parentMessage) {
					parentMessage.childrenIds.push(messageId);
					history.messages[parentMessage.id] = parentMessage;
				}

				history.messages[messageId] = responseMessage;
				parentMessage = responseMessage;
				currentParentId = messageId;
			}
		}

		history.currentId = currentParentId;
		await tick();

		if (autoScroll) {
			scrollToBottom();
		}

		if (messages.length === 0) {
			await initChatHandler(history);
		} else {
			await saveChatHandler($chatId, history);
		}
	};

	const chatCompletionEventHandler = async (
		data,
		message,
		targetChatId,
		expectedCatalogGeneration = modeProfileCatalogGeneration
	) => {
		if (
			$chatId !== targetChatId ||
			!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)
		) {
			return;
		}
		const {
			id,
			done,
			choices,
			content,
			output,
			sources,
			metadata,
			selected_model_id,
			error,
			usage
		} = data;

		// Store raw OR-aligned output items from backend
		if (output) {
			message.output = output;
			message.content = getOutputText(output);
			dispatchCallOverlayAudio(message);
		}

		if (error) {
			await handleOpenAIError(error, message);
			if (
				$chatId !== targetChatId ||
				!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)
			) {
				return;
			}
		}

		if (sources && !message?.sources) {
			message.sources = sources;
		}

		if (metadata) {
			message.metadata = {
				...(message.metadata ?? {}),
				...metadata
			};
		}

		if (choices && !output) {
			if (choices[0]?.message?.content) {
				// Non-stream response
				message.content += choices[0]?.message?.content;
				dispatchCallOverlayAudio(message);
			} else {
				// Stream response
				let value = choices[0]?.delta?.content ?? '';
				if (message.content == '' && value == '\n') {
					console.log('Empty response');
				} else {
					message.content += value;

					if (navigator.vibrate && ($settings?.hapticFeedback ?? false)) {
						navigator.vibrate(5);
					}
					dispatchCallOverlayAudio(message);
				}
			}
		}

		if (content && !output) {
			// REALTIME_CHAT_SAVE is disabled
			message.content = content;

			if (navigator.vibrate && ($settings?.hapticFeedback ?? false)) {
				navigator.vibrate(5);
			}
			dispatchCallOverlayAudio(message);
		}

		if (selected_model_id) {
			message.selectedModelId = selected_model_id;
			message.arena = true;
		}

		if (usage) {
			message.usage = usage;
		}

		history.messages[message.id] = message;

		if (done) {
			message.done = true;
			const visibleContent =
				getOutputText(message?.output) || removeAllDetails(message?.content ?? '');

			if ($settings.responseAutoCopy) {
				copyToClipboard(visibleContent);
			}

			if ($settings.responseAutoPlayback && !$showCallOverlay) {
				await tick();
				if (
					$chatId !== targetChatId ||
					!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)
				) {
					return;
				}
				document.getElementById(`speak-button-${message.id}`)?.click();
			}

			// Emit chat event for TTS (only when call overlay is active)
			dispatchCallOverlayAudio(message, true);
			eventTarget.dispatchEvent(
				new CustomEvent('chat:finish', {
					detail: {
						id: message.id,
						content: visibleContent
					}
				})
			);

			history.messages[message.id] = message;

			await tick();
			if (
				$chatId !== targetChatId ||
				!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)
			) {
				return;
			}
			if (autoScroll) {
				scrollToBottom();
			}

			// Fire-and-forget: run chatCompletedHandler for background work
			// (outlet filters, chat save, title gen, follow-ups, tags)
			// without blocking the user from sending new messages.
			chatCompletedHandler(targetChatId, message.model, message.id, expectedCatalogGeneration);

			// Process next queued request if any
			await processNextInQueue(targetChatId, expectedCatalogGeneration);
			if (
				$chatId !== targetChatId ||
				!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)
			) {
				return;
			}
		}

		console.log(data);
		await tick();
	};

	//////////////////////////
	// Chat functions
	//////////////////////////

	const submitPrompt = async (inputContent, inputFiles) => {
		const _files = structuredClone(inputFiles);

		chatFiles.push(
			..._files.filter(
				(item) =>
					['doc', 'text', 'note', 'chat', 'folder', 'collection'].includes(item.type) ||
					(item.type === 'file' && !(item?.content_type ?? '').startsWith('image/'))
			)
		);
		chatFiles = chatFiles.filter(
			// Remove duplicates
			(item, index, array) => array.findIndex((i) => equal(i, item)) === index
		);

		// Create user message
		let userMessageId = uuidv4();
		let userMessage = {
			id: userMessageId,
			parentId: history.currentId ?? null,
			childrenIds: [],
			role: 'user',
			content: inputContent,
			files: _files.length > 0 ? _files : undefined,
			timestamp: Math.floor(Date.now() / 1000), // Unix epoch
			models: selectedModels
		};

		// Add message to history and Set currentId to messageId
		history.messages[userMessageId] = userMessage;

		// Append messageId to childrenIds of parent message
		if (history.currentId !== null) {
			history.messages[history.currentId].childrenIds.push(userMessageId);
		}

		history.currentId = userMessageId;

		// focus on chat input (skip during voice call to avoid triggering mobile keyboard)
		if (!$showCallOverlay) {
			const chatInput = document.getElementById('chat-input');
			chatInput?.focus();
		}

		saveSessionSelectedModels();

		await sendMessage(history, userMessageId);
	};

	const handleManualCompact = async () => {
		if (!contextCompactionEnabled) {
			toast.message($i18n.t('Context compaction is disabled'));
			return;
		}

		if (!$chatId || !history?.currentId) {
			toast.message($i18n.t('No chat to compact'));
			return;
		}

		const currentMessage = history.messages?.[history.currentId];
		if (
			generating ||
			taskIds?.length ||
			(currentMessage?.role === 'assistant' && !currentMessage.done)
		) {
			toast.warning($i18n.t('Wait for the current response to finish before compacting.'));
			return;
		}

		const model = atSelectedModel?.id ?? selectedModels.find((modelId) => modelId);
		const toastId = toast.loading($i18n.t('Compacting context...'));

		try {
			const result = await compactChatById(localStorage.token, $chatId, model);
			serverContextUsage = result?.context_usage ?? serverContextUsage;

			if (result?.compacted) {
				toast.success($i18n.t('Context compacted'), { id: toastId });
			} else {
				const skippedReason =
					result?.reason === 'too_short'
						? $i18n.t('Chat is too short to compact')
						: result?.reason === 'empty'
							? $i18n.t('No chat to compact')
							: result?.reason === 'disabled'
								? $i18n.t('Context compaction is disabled')
								: $i18n.t('Nothing to compact');
				toast.message(skippedReason, { id: toastId });
			}

			await loadChat();
		} catch (error) {
			const message = error?.detail ?? error?.message ?? $i18n.t('Context compaction failed');
			toast.error(message, { id: toastId });
		} finally {
			messageInput?.setText('');
			prompt = '';
			document.getElementById('chat-input')?.focus();
		}
	};

	const handleStatusCommand = () => {
		messageInput?.showStatus();
		messageInput?.setText('');
		prompt = '';
		document.getElementById('chat-input')?.focus();
	};

	const handleForkChat = async (messageId: string | null = null) => {
		if (!$chatId || !history?.currentId) {
			toast.message($i18n.t('No chat to fork'));
			return;
		}
		if (!($user?.role === 'admin' || ($user?.permissions?.chat?.import ?? true))) {
			toast.error($i18n.t('Access prohibited'));
			return;
		}

		const currentMessage = history.messages?.[history.currentId];
		if (
			generating ||
			taskIds?.length ||
			(currentMessage?.role === 'assistant' && !currentMessage.done)
		) {
			toast.warning($i18n.t('Wait for the current response to finish before forking.'));
			return;
		}

		const toastId = toast.loading($i18n.t('Forking chat...'));

		try {
			const result = await forkChatById(
				localStorage.token,
				$chatId,
				messageId ?? history.currentId
			);

			if (result?.id) {
				if (!embedded) {
					await goto(`/c/${result.id}`);
					await refreshChatList(localStorage.token, { refreshPinned: true });
				}
				toast.success($i18n.t('Chat forked'), { id: toastId });
			} else {
				toast.error($i18n.t('Failed to fork chat'), { id: toastId });
			}
		} catch (error) {
			toast.error(`${error}`, { id: toastId });
		} finally {
			messageInput?.setText('');
			prompt = '';
			document.getElementById('chat-input')?.focus();
		}
	};

	const submitHandler = async (userPrompt, { _raw = false } = {}) => {
		console.log('submitHandler', userPrompt, $chatId);

		const _selectedModels = selectedModels.map((modelId) =>
			$models.map((m) => m.id).includes(modelId) ? modelId : ''
		);

		if (!equal(selectedModels, _selectedModels)) {
			selectedModels = _selectedModels;
		}

		if (String(userPrompt).trim() === '/compact') {
			await handleManualCompact();
			return;
		}
		if (String(userPrompt).trim() === '/status') {
			handleStatusCommand();
			return;
		}
		if (String(userPrompt).trim() === '/fork') {
			await handleForkChat();
			return;
		}

		if (pendingOAuthTools.length > 0) {
			toast.warning($i18n.t('Please connect all required integrations before sending a message'));
			return;
		}
		if (userPrompt === '' && files.length === 0) {
			toast.error($i18n.t('Please enter a prompt'));
			return;
		}
		if (selectedModels.includes('')) {
			toast.error($i18n.t('Model not selected'));
			return;
		}
		const form = getChatVariablesForm(selectedModelIds, chatVariables, $models);
		if (form.conflicts.length > 0) {
			showChatVariablesModal = true;
			toast.error($i18n.t('Chat Variables have conflicting model definitions'));
			return;
		}
		if (form.missing || form.empty) {
			showChatVariablesModal = true;
			return;
		}

		if (
			files.length > 0 &&
			files.filter((file) => file.type !== 'image' && file.status === 'uploading').length > 0
		) {
			toast.error(
				$i18n.t(`Oops! There are files still uploading. Please wait for the upload to complete.`)
			);
			return;
		}

		if (
			($config?.file?.max_count ?? null) !== null &&
			files.length + chatFiles.length > $config?.file?.max_count
		) {
			toast.error(
				$i18n.t(`You can only chat with a maximum of {{maxCount}} file(s) at a time.`, {
					maxCount: $config?.file?.max_count
				})
			);
			return;
		}

		if (
			$config?.features?.enable_web_search_confirmation &&
			webSearchActive &&
			!webSearchConfirmed
		) {
			pendingWebSearchPrompt = userPrompt ?? '';
			openWebSearchConfirm();
			return;
		}

		// Check if the assistant is still generating the main response
		// (don't block on background tasks like title gen, follow-ups, tags)
		const lastMessage = history.currentId ? history.messages[history.currentId] : null;
		const isGenerating = lastMessage && lastMessage.role === 'assistant' && !lastMessage.done;

		if (isGenerating) {
			if ($settings?.enableMessageQueue ?? true) {
				// Enqueue the request
				const _files = structuredClone(files);
				chatRequestQueues.update((q) => ({
					...q,
					[$chatId]: [...(q[$chatId] ?? []), { id: uuidv4(), prompt: userPrompt, files: _files }]
				}));
				await runAcceptedSubmitDraftCriticalSection(async () => {
					await messageInput?.setText('');
					prompt = '';
					files = [];
				});
				return;
			} else {
				// Interrupt: stop current generation and proceed
				await stopResponse();
				await tick();
			}
		}

		if (history?.currentId) {
			const currentMessage = history.messages[history.currentId];

			if (currentMessage.error && !currentMessage.content) {
				// Error in response
				toast.error($i18n.t(`Oops! There was an error in the previous response.`));
				return;
			}
		}

		const _files = structuredClone(files);
		await runAcceptedSubmitDraftCriticalSection(async () => {
			await messageInput?.setText('');
			prompt = '';
			files = [];
		});

		await submitPrompt(userPrompt, _files);
	};

	const sendMessage = async (
		_history,
		parentId: string,
		{
			messages = null,
			modelId = null,
			modelIdx = null,
			regenerationPrompt = null
		}: {
			messages?: any[] | null;
			modelId?: string | null;
			modelIdx?: number | null;
			regenerationPrompt?: string | null;
		} = {}
	) => {
		if (autoScroll) {
			scrollToBottom();
		}

		let _chatId = JSON.parse(JSON.stringify($chatId));
		_history = structuredClone(_history);

		const responseMessageIds: Record<PropertyKey, string> = {};
		// If modelId is provided, use it, else use selected model
		let selectedModelIds = modelId
			? [modelId]
			: atSelectedModel !== undefined
				? [atSelectedModel.id]
				: selectedModels;
		selectedModelIds = resolveConversationModeRequestModels(selectedModelIds, conversationMode);

		// Create response messages for each selected model
		// Build message_ids list: [{model_id, message_id, modelIdx}, ...]
		// Uses an array instead of a dict to support duplicate model IDs in side-by-side chat.
		// modelIdx identifies each side-by-side column so the backend can persist it; without
		// it, duplicate models collapse into one another when the chat is reloaded.
		const messageIdsList: Array<{ model_id: string; message_id: string; modelIdx: number }> = [];
		for (const [_modelIdx, modelId] of selectedModelIds.entries()) {
			const model = $models.filter((m) => m.id === modelId).at(0);

			if (model) {
				let responseMessageId = uuidv4();
				let responseMessage = {
					parentId: parentId,
					id: responseMessageId,
					childrenIds: [],
					role: 'assistant',
					content: '',
					done: false,
					model: model.id,
					modelName: model.name ?? model.id,
					modelIdx: modelIdx ? modelIdx : _modelIdx,
					timestamp: Math.floor(Date.now() / 1000) // Unix epoch
				};

				// Add message to history and Set currentId to messageId
				history.messages[responseMessageId] = responseMessage;
				history.currentId = responseMessageId;

				// Append messageId to childrenIds of parent message
				if (parentId !== null && history.messages[parentId]) {
					history.messages[parentId].childrenIds = [
						...history.messages[parentId].childrenIds,
						responseMessageId
					];
				}

				responseMessageIds[`${modelId}-${modelIdx ? modelIdx : _modelIdx}`] = responseMessageId;
				messageIdsList.push({
					model_id: modelId,
					message_id: responseMessageId,
					modelIdx: modelIdx ? modelIdx : _modelIdx
				});
			}
		}
		history = history;

		// Empty embedded drafts create their backing chat only when the first message is sent.
		if (!_chatId) {
			if (embedded && onCreateEmbeddedChat) {
				const createdChat = await onCreateEmbeddedChat();
				if (!createdChat?.id) {
					toast.error($i18n.t('Failed to create chat'));
					return;
				}

				chat = createdChat;
				_chatId = createdChat.id;
				loadedChatIdProp = _chatId;
				await chatId.set(_chatId);
				await chatTitle.set(createdChat?.chat?.title ?? createdChat?.title ?? $i18n.t('Chat'));

				params = structuredClone(createdChat?.chat?.params ?? {});
				delete params.note_id;
				chatFiles = mergeFiles(chatFiles, createdChat?.chat?.files ?? []);
				await onSelectEmbeddedChat?.(_chatId);
			} else if ($temporaryChatEnabled) {
				_chatId = createTemporaryChatId($socket?.id);
				await chatId.set(_chatId);
			}
			await tick();
		}

		await tick();

		// Re-clone history so sendMessageSocket gets the response messages we just added
		_history = structuredClone(history);

		// Vision capability check
		for (const mid of selectedModelIds) {
			const model = $models.filter((m) => m.id === mid).at(0);
			if (model) {
				const hasImages = createMessagesList(_history, parentId).some((message) =>
					message.files?.some(
						(file) => file.type === 'image' || (file?.content_type ?? '').startsWith('image/')
					)
				);

				if (
					hasImages &&
					!(model.info?.meta?.capabilities?.vision ?? true) &&
					!imageGenerationEnabled
				) {
					toast.error(
						$i18n.t('Model {{modelName}} is not vision capable', {
							modelName: model.name ?? model.id
						})
					);
				}
			}
		}

		// Single request — backend fans out to all models
		const primaryModelId = selectedModelIds[0];
		const primaryModel = $models.filter((m) => m.id === primaryModelId).at(0);
		const primaryResponseMessageId = messageIdsList[0]?.message_id;

		if (primaryModel && primaryResponseMessageId) {
			const chatEventEmitter = await getChatEventEmitter(primaryModel.id, _chatId);

			try {
				scrollToBottom();
				await sendMessageSocket(
					primaryModel,
					messages && messages.length > 0
						? messages
						: createMessagesList(_history, primaryResponseMessageId),
					_history,
					primaryResponseMessageId,
					_chatId,
					{
						// Always forward the message_ids list (not just for multi-model sends) so the
						// backend persists each response's modelIdx — including single-column
						// regenerations in a duplicate-model chat, which would otherwise lose their
						// column identity and collapse on reload.
						messageIdsList: messageIdsList.length > 0 ? messageIdsList : undefined,
						regenerationPrompt
					}
				);
			} finally {
				if (chatEventEmitter) clearInterval(chatEventEmitter);
			}
		}
	};

	const getStopTokens = () => {
		const stop = params?.stop ?? $settings?.params?.stop;
		if (!stop) return undefined;

		const tokens = Array.isArray(stop) ? stop : stop.split(',').map((s) => s.trim());

		return tokens
			.filter(Boolean)
			.map((token) => decodeURIComponent(JSON.parse(`"${token.replace(/"/g, '\\"')}"`)));
	};

	const sendMessageSocket = async (
		model,
		_messages,
		_history,
		responseMessageId,
		_chatId,
		{
			messageIdsList,
			regenerationPrompt,
			continueResponse = false
		}: {
			messageIdsList?: Array<{ model_id: string; message_id: string }>;
			regenerationPrompt?: string | null;
			continueResponse?: boolean;
		} = {}
	) => {
		const directToolServersPermitted = isDirectToolServersPermitted($user);
		const directTerminalIds = ((($settings as any)?.terminalServers ?? []) as any[])
			.map((server) => server?.url)
			.filter((id): id is string => typeof id === 'string');
		const requestContext = captureConversationModeRequestContext({
			mode: conversationMode,
			revisionHint: modeProfileRevisionId,
			authority: modeProfileCapabilityAuthority,
			profile: getModeProfile(),
			model,
			selections: getModeProfileSelections({ includePendingOAuthTools: false }),
			overrideFields: modeProfileCapabilityOverrideFields,
			featureState: {
				availableFeatureIds: getModeProfileAvailableFeatureIds(),
				voice: Boolean($showCallOverlay),
				memory: Boolean($settings?.memory ?? $config?.features?.enable_memories ?? false),
				webSearchAlways: ($settings?.webSearch ?? false) === 'always',
				imageGenerationUserOverride,
				imageGenerationGloballyEnabled: Boolean($config?.features?.enable_image_generation),
				imageGenerationAllowed: hasImageGenerationAccess()
			},
			directToolServersPermitted,
			directTerminalIds
		});
		const externalCatalogRequest = getModeProfileExternalCatalogRequest(
			requestContext.model as Model,
			requestContext
		);
		const reasoning = buildModelReasoningPayload(requestContext.model, reasoningEffort);

		const modeProfileRequest = await resolveModeProfileRequest(
			requestContext,
			externalCatalogRequest
		);

		const responseMessage = _history.messages[responseMessageId];
		const userMessage = _history.messages[responseMessage.parentId];

		const chatMessageFiles = _messages
			.filter((message) => message.files)
			.flatMap((message) => message.files);

		// Filter chatFiles to only include files that are in the chatMessageFiles
		chatFiles = chatFiles.filter((item) => {
			const fileExists = chatMessageFiles.some((messageFile) => messageFile.id === item.id);
			return fileExists;
		});

		let files = structuredClone(chatFiles);
		files.push(
			...(userMessage?.files ?? []).filter(
				(item) =>
					['doc', 'text', 'note', 'chat', 'collection', 'folder'].includes(item.type) ||
					(item.type === 'file' && !(item?.content_type ?? '').startsWith('image/'))
			)
		);
		// Remove duplicates
		files = files.filter((item, index, array) => array.findIndex((i) => equal(i, item)) === index);

		scrollToBottom();
		eventTarget.dispatchEvent(
			new CustomEvent('chat:start', {
				detail: {
					id: responseMessageId
				}
			})
		);
		await tick();

		let userLocation;
		if ($settings?.userLocation) {
			userLocation = await getAndUpdateUserLocation(localStorage.token).catch((err) => {
				console.error(err);
				return undefined;
			});
		}

		const stream =
			requestContext.model?.info?.params?.stream_response ??
			$settings?.params?.stream_response ??
			params?.stream_response ??
			true;
		// Always include system prompt — backend extracts it and prepends to DB messages.
		// Only temp chats need conversation messages (persisted chats load from DB).
		let messages: any[] = [
			params?.system || $settings.system
				? { role: 'system', content: `${params?.system ?? $settings?.system ?? ''}` }
				: undefined
		].filter(Boolean);

		if ($temporaryChatEnabled) {
			messages = [
				...messages,
				..._messages.map((message) => ({
					...message,
					...(message.output && message.role === 'assistant'
						? { output: message.output }
						: { content: processDetails(message.content) })
				}))
			].filter((message) => message);

			messages = messages
				.map((message) => {
					const imageFiles = (message?.files ?? []).filter(
						(file) => file.type === 'image' || (file?.content_type ?? '').startsWith('image/')
					);

					if (message.output && message.role === 'assistant') {
						return { role: message.role, output: message.output };
					}

					if (message.role === 'user' && imageFiles.length > 0) {
						return {
							role: message.role,
							content: [
								{
									type: 'text',
									text: message?.merged?.content ?? message.content
								},
								...imageFiles.map((file) => ({
									type: 'image_url',
									image_url: {
										url: file.url
									}
								}))
							]
						};
					}

					return {
						role: message.role,
						content: message?.merged?.content ?? message.content
					};
				})
				.filter(
					(message) =>
						message?.role === 'user' || message?.content?.trim() || message?.output?.length
				);
		}

		const modelCapabilities = (requestContext.model.info?.meta?.capabilities ?? {}) as Record<
			string,
			unknown
		>;
		const functionCallingEnabled = modelCapabilities.function_calling !== false;
		// Terminal requires the same function-calling allowance as its resolver path.
		const terminalEnabled = functionCallingEnabled && modelCapabilities.terminal !== false;
		const capabilityRequest = serializeConversationModeCapabilityRequest({
			authority: requestContext.authority,
			overrideFields: requestContext.overrideFields,
			selections: modeProfileRequest.effective,
			features: getConversationModeRequestFeatures(requestContext, modeProfileRequest.effective),
			directToolServersPermitted: requestContext.directToolServersPermitted,
			directTerminalIds: requestContext.directTerminalIds,
			functionCallingEnabled,
			terminalEnabled
		});
		const toolServersRequest = serializeConversationModeToolServers({
			emitToolServers: capabilityRequest.emitToolServers,
			toolServerIds: capabilityRequest.toolServerIds,
			terminalId: (capabilityRequest.request as { terminal_id?: unknown }).terminal_id,
			directToolServersPermitted: requestContext.directToolServersPermitted,
			toolServers: modeProfileRequest.catalogView.toolServers,
			terminalServers: modeProfileRequest.catalogView.terminalServers
		});
		const useChatVariablesFallback =
			!_chatId || $temporaryChatEnabled || isTemporaryChatId(_chatId);

		const res = await generateOpenAIChatCompletion(
			localStorage.token,
			{
				stream: stream,
				model: requestContext.model.id,
				chat_mode: requestContext.mode,
				mode_profile_revision_id: requestContext.revisionHint ?? undefined,
				...(messages.length > 0 ? { messages } : {}),
				...(reasoning ? { reasoning } : {}),
				params: {
					...$settings?.params,
					...params,
					stop: getStopTokens()
				},

				files: (files?.length ?? 0) > 0 ? files : undefined,

				...capabilityRequest.request,
				...toolServersRequest,
				variables: {
					...getPromptVariables(
						$user?.name,
						$settings?.userLocation ? userLocation : undefined,
						$user?.email
					)
				},
				...(useChatVariablesFallback ? { chat_variables: chatVariables } : {}),
				model_item: requestContext.model,

				session_id: $socket?.id,
				chat_id: _chatId || undefined,
				folder_id: $selectedFolder?.id ?? undefined,

				id: responseMessageId,
				...(messageIdsList ? { message_ids: messageIdsList } : {}),
				parent_id: userMessage?.parentId ?? null,
				user_message: userMessage,
				...(regenerationPrompt ? { regeneration_prompt: regenerationPrompt } : {}),
				...(continueResponse ? { assistant_message_id: responseMessageId } : {}),

				background_tasks: {
					...(!$temporaryChatEnabled &&
					(!_chatId ||
						(embedded &&
							(userMessage?.parentId ?? null) === null &&
							createMessagesList(_history, responseMessageId).length === 2))
						? {
								title_generation: $settings?.title?.auto ?? true,
								tags_generation: $settings?.autoTags ?? true
							}
						: {}),
					follow_up_generation: $settings?.autoFollowUps ?? true
				},

				...(stream && (requestContext.model.info?.meta?.capabilities?.usage ?? false)
					? {
							stream_options: {
								include_usage: true
							}
						}
					: {})
			},
			`${WEBUI_BASE_URL}/api`
		).catch(async (error) => {
			console.log(error);

			let errorMessage = error;
			if (error?.error?.message) {
				errorMessage = error.error.message;
			} else if (error?.message) {
				errorMessage = error.message;
			}

			if (typeof errorMessage === 'object') {
				errorMessage = $i18n.t(`Uh-oh! There was an issue with the response.`);
			}

			toast.error(`${errorMessage}`);
			responseMessage.error = {
				content: error
			};

			responseMessage.done = true;

			history.messages[responseMessageId] = responseMessage;
			history.currentId = responseMessageId;

			return null;
		});

		if (res) {
			if (res.error) {
				await handleOpenAIError(res.error, responseMessage);
			} else {
				if (res.agent_run_id) {
					responseMessage.agent_run_id = res.agent_run_id;
					history.messages[responseMessageId] = {
						...history.messages[responseMessageId],
						agent_run_id: res.agent_run_id
					};
				}

				// Backend returns task_ids (multi-model) or task_id (single model)
				const newTaskIds = res.task_ids ?? (res.task_id ? [res.task_id] : []);
				if (newTaskIds.length > 0) {
					taskIds = [...(taskIds ?? []), ...newTaskIds];
				}

				// Backend returns chat_id for new chats — set store + URL.
				// Only update if the user hasn't navigated to a different chat
				// while the request was in flight (prevents overwriting $chatId
				// and causing spurious toast notifications / state duplication).
				if (res.chat_id && $chatId !== res.chat_id && $chatId === _chatId) {
					await chatId.set(res.chat_id);
					if (!$temporaryChatEnabled && !embedded) {
						window.history.replaceState(history.state, '', `/c/${res.chat_id}`);
						await refreshChatList(localStorage.token);

						// Persist chat-level params (system prompt, advanced
						// params) that the backend doesn't receive in the
						// chat completion request.  Files are now persisted
						// by the backend at chat creation time.
						if (Object.keys(params).length > 0) {
							await updateChatById(localStorage.token, res.chat_id, {
								params: params
							});
						}
					}
				}
			}
		}

		await tick();
		scheduleResponseScrollToBottom();
	};

	const handleOpenAIError = async (error, responseMessage) => {
		let errorMessage = '';
		let innerError;

		if (error) {
			innerError = error;
		}

		console.error(innerError);
		if ('detail' in innerError) {
			// FastAPI error
			toast.error(innerError.detail);
			errorMessage = innerError.detail;
		} else if ('error' in innerError) {
			// OpenAI error
			if ('message' in innerError.error) {
				toast.error(innerError.error.message);
				errorMessage = innerError.error.message;
			} else {
				toast.error(innerError.error);
				errorMessage = innerError.error;
			}
		} else if ('message' in innerError) {
			// OpenAI error
			toast.error(innerError.message);
			errorMessage = innerError.message;
		}

		responseMessage.error = {
			content: $i18n.t(`Uh-oh! There was an issue with the response.`) + '\n' + errorMessage
		};
		responseMessage.done = true;

		if (responseMessage.statusHistory) {
			responseMessage.statusHistory = responseMessage.statusHistory.filter(
				(status) => status.action !== 'knowledge_search'
			);
		}

		history.messages[responseMessage.id] = responseMessage;
	};

	const stopResponse = async (processQueue = true) => {
		const targetChatId = $chatId;
		const expectedCatalogGeneration = modeProfileCatalogGeneration;
		if (taskIds) {
			if (targetChatId) {
				await stopTasksByChatId(localStorage.token, targetChatId).catch((error) => {
					toast.error(`${error}`);
					return null;
				});
			} else {
				for (const taskId of taskIds) {
					const res = await stopTask(localStorage.token, taskId).catch((error) => {
						toast.error(`${error}`);
						return null;
					});
				}
			}
			if (
				$chatId !== targetChatId ||
				!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)
			) {
				return;
			}

			taskIds = null;

			const responseMessage = history.messages[history.currentId];
			// Set all response messages to done
			if (responseMessage.parentId && history.messages[responseMessage.parentId]) {
				for (const messageId of history.messages[responseMessage.parentId].childrenIds) {
					history.messages[messageId].done = true;
				}
			}

			history.messages[history.currentId] = responseMessage;

			if (shouldAutoScrollResponse()) {
				scrollToBottom();
			}
		}

		if (generating) {
			generating = false;
			generationController?.abort();
			generationController = null;
		}

		if (
			processQueue &&
			targetChatId &&
			$chatId === targetChatId &&
			isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)
		) {
			await processNextInQueue(targetChatId, expectedCatalogGeneration);
		}
	};

	const submitMessage = async (parentId, prompt) => {
		let userPrompt = prompt;
		let userMessageId = uuidv4();

		let userMessage = {
			id: userMessageId,
			parentId: parentId,
			childrenIds: [],
			role: 'user',
			content: userPrompt,
			models: selectedModels,
			timestamp: Math.floor(Date.now() / 1000) // Unix epoch
		};

		if (parentId !== null) {
			history.messages[parentId].childrenIds = [
				...history.messages[parentId].childrenIds,
				userMessageId
			];
		}

		history.messages[userMessageId] = userMessage;
		history.currentId = userMessageId;

		await tick();

		if (autoScroll) {
			scrollToBottom();
		}

		await sendMessage(history, userMessageId);
	};

	const regenerateResponse = async (message, suggestionPrompt = null) => {
		console.log('regenerateResponse');

		if (history.currentId) {
			let userMessage = history.messages[message.parentId];

			if (!userMessage) {
				toast.error($i18n.t('Parent message not found'));
				return;
			}

			if (autoScroll) {
				scrollToBottom();
			}

			await sendMessage(history, userMessage.id, {
				...(suggestionPrompt
					? {
							messages: createMessagesList(history, message.id),
							regenerationPrompt: suggestionPrompt
						}
					: {}),
				...((userMessage?.models ?? [...selectedModels]).length > 1
					? {
							// If multiple models are selected, use the model from the message
							modelId: message.model,
							modelIdx: message.modelIdx
						}
					: {})
			});
		}
	};

	const continueResponse = async () => {
		console.log('continueResponse');
		const _chatId = JSON.parse(JSON.stringify($chatId));

		if (history.currentId && history.messages[history.currentId].done == true) {
			const responseMessage = history.messages[history.currentId];
			responseMessage.done = false;
			await tick();

			const model = $models
				.filter((m) => m.id === (responseMessage?.selectedModelId ?? responseMessage.model))
				.at(0);

			if (model) {
				await sendMessageSocket(
					model,
					createMessagesList(history, responseMessage.id),
					history,
					responseMessage.id,
					_chatId,
					{ continueResponse: true }
				);
			}
		}
	};

	const mergeResponses = async (messageId, responses, _chatId) => {
		console.log('mergeResponses', messageId, responses);
		const message = history.messages[messageId];
		const mergedResponse = {
			status: true,
			content: ''
		};
		message.merged = mergedResponse;
		history.messages[messageId] = message;

		try {
			generating = true;
			const [res, controller] = await generateMoACompletion(
				localStorage.token,
				message.model ?? '',
				message.parentId ? history.messages[message.parentId].content : '',
				responses
			);

			if (res && res.ok && res.body && generating) {
				generationController = controller as AbortController;
				const textStream = await createOpenAITextStream(
					res.body,
					Boolean($settings?.splitLargeChunks ?? false)
				);
				for await (const update of textStream) {
					const { value, done, sources, error, usage } = update;
					if (error || done) {
						generating = false;
						generationController = null;
						break;
					}

					if (mergedResponse.content == '' && value == '\n') {
						continue;
					} else {
						mergedResponse.content += value;
						history.messages[messageId] = message;
					}
				}

				await saveChatHandler(_chatId, history);
			} else {
				console.error(res);
			}
		} catch (e) {
			console.error(e);
		}
	};

	const initChatHandler = async (history) => {
		let _chatId = $chatId;
		const selectedFolderId = $selectedFolder?.id;

		if (!$temporaryChatEnabled) {
			chat = await createNewChat(
				localStorage.token,
				{
					id: _chatId,
					title: $i18n.t('New Chat'),
					mode: conversationMode,
					models: selectedModels,
					system: $settings.system ?? undefined,
					params: params,
					history: history,
					messages: createMessagesList(history, history.currentId),
					tags: [],
					timestamp: Date.now()
				},
				$selectedFolder?.id,
				chatVariables
			);

			_chatId = chat.id;
			bindCanonicalModeProfileRevision(chat?.mode_profile_revision_id);
			await chatId.set(_chatId);

			if (!embedded) {
				window.history.replaceState(history.state, '', `/c/${_chatId}`);
			}

			await tick();

			if (!embedded) {
				await refreshChatList(localStorage.token);
			}

			if (selectedFolderId) {
				await refreshFolderChatLists(selectedFolderId, chat);
			}

			selectedFolder.set(null);
		} else {
			_chatId = createTemporaryChatId($socket?.id);
			await chatId.set(_chatId);
		}
		await tick();

		return _chatId;
	};

	const saveChatHandler = async (_chatId, history) => {
		if ($chatId == _chatId) {
			if (!$temporaryChatEnabled) {
				chat = await updateChatById(localStorage.token, _chatId, {
					mode: conversationMode,
					models: selectedModels,
					history: history,
					messages: createMessagesList(history, history.currentId),
					params: params,
					files: chatFiles
				});
			}
		}
	};

	const saveControls = async () => {
		if (!$chatId || $temporaryChatEnabled) return;
		const targetChatId = $chatId;
		const expectedCatalogGeneration = modeProfileCatalogGeneration;
		const loaded = chat?.chat ?? {};
		if (equal(params, loaded.params ?? {}) && equal(chatFiles, loaded.files ?? [])) return;

		const res = await updateChatById(localStorage.token, targetChatId, {
			params,
			files: chatFiles
		}).catch((err) => {
			console.error('[controls autosave]', err);
			return null;
		});
		if (
			!res ||
			$chatId !== targetChatId ||
			!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)
		)
			return;
		// Refresh the dedupe baseline so a later revert still saves.
		chat = res;
		bindCanonicalModeProfileRevision(chat?.mode_profile_revision_id);
	};

	const MAX_DRAFT_LENGTH = 5000;
	let saveDraftTimeout: ReturnType<typeof setTimeout> | null = null;
	let acceptedSubmitDraftPersistenceSuppressed = false;
	const shouldAutosaveModeProfileDraft = () =>
		!$temporaryChatEnabled &&
		(Boolean($chatId) ||
			!Object.values(history.messages).some((message: any) => message?.role === 'user'));
	const createModeProfileDraftSnapshot = (input: any) => ({
		...(input ?? {}),
		selectedToolIds: getModeProfileSelectedToolIds(),
		selectedSkillIds: [...selectedSkillIds],
		selectedFilterIds: [...selectedFilterIds],
		webSearchEnabled,
		codeInterpreterEnabled,
		imageGenerationEnabled,
		conversationMode: conversationMode,
		modeProfileRevisionId,
		modeProfileCapabilitySnapshotVersion: modeProfileControlsReady ? 1 : undefined,
		modeProfileCapabilityAuthority: modeProfileControlsReady
			? modeProfileCapabilityAuthorityController.snapshot()
			: undefined,
		modeProfileCapabilityOverrideFields: modeProfileControlsReady
			? (modeProfileCapabilityOverrideFields ?? undefined)
			: undefined,
		selectedTerminalId: $selectedTerminalId ?? null,
		imageGenerationUserOverride
	});
	const getCurrentModeProfileDraftInput = () => ({
		prompt,
		files: files
			.filter((file) => file.type !== 'image')
			.map((file) => ({
				...file,
				user: undefined,
				access_grants: undefined
			})),
		reasoningEffort
	});

	const saveDraft = async (
		draft: any,
		chatId: string | null = null,
		{ immediate = false }: { immediate?: boolean } = {}
	) => {
		if (acceptedSubmitDraftPersistenceSuppressed) return;

		if (saveDraftTimeout) {
			clearTimeout(saveDraftTimeout);
		}

		if (draft.prompt !== null && draft.prompt.length < MAX_DRAFT_LENGTH) {
			const persistDraft = async () => {
				await sessionStorage.setItem(
					`chat-input${chatId ? `-${chatId}` : ''}`,
					JSON.stringify(draft)
				);
			};
			if (immediate) {
				await persistDraft();
			} else {
				saveDraftTimeout = setTimeout(persistDraft, 500);
			}
		} else {
			sessionStorage.removeItem(`chat-input${chatId ? `-${chatId}` : ''}`);
		}
	};

	const persistFinalizedModeProfileDraftSnapshot = async (
		expectedCatalogGeneration = modeProfileCatalogGeneration
	) => {
		if ($temporaryChatEnabled || acceptedSubmitDraftPersistenceSuppressed) return;
		if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
		await tick();
		if (!isModeProfileCatalogGenerationCurrent(expectedCatalogGeneration)) return;
		const draftChatId = modeProfileDraftId.startsWith('draft:') ? null : $chatId || null;
		await saveDraft(
			createModeProfileDraftSnapshot(getCurrentModeProfileDraftInput()),
			draftChatId,
			{ immediate: true }
		);
	};

	const saveModeProfileCapabilityAuthority = () => {
		if (
			$temporaryChatEnabled ||
			acceptedSubmitDraftPersistenceSuppressed ||
			!latestModeProfileDraftInput
		)
			return;
		saveDraft(createModeProfileDraftSnapshot(latestModeProfileDraftInput), $chatId || null);
	};

	const clearDraft = async (chatId: string | null = null) => {
		if (saveDraftTimeout) {
			clearTimeout(saveDraftTimeout);
		}
		await sessionStorage.removeItem(`chat-input${chatId ? `-${chatId}` : ''}`);
	};

	const runAcceptedSubmitDraftCriticalSection = async (clearInput: () => void | Promise<void>) => {
		acceptedSubmitDraftPersistenceSuppressed = true;
		try {
			await clearDraft($chatId || null);
			await clearInput();
			await tick();
		} finally {
			acceptedSubmitDraftPersistenceSuppressed = false;
		}
	};

	const moveChatHandler = async (chatId, folderId) => {
		if (chatId && folderId) {
			const res = await updateChatFolderIdById(localStorage.token, chatId, folderId).catch(
				(error) => {
					toast.error(`${error}`);
					return null;
				}
			);

			if (res) {
				await refreshChatList(localStorage.token, { refreshPinned: true });
				await refreshFolderChatLists();

				toast.success($i18n.t('Chat moved successfully'));
			}
		} else {
			toast.error($i18n.t('Failed to move chat'));
		}
	};

	const archiveChatHandler = async (id: string) => {
		try {
			await archiveChatById(localStorage.token, id);
			initNewChat();
			await goto('/');
			await refreshChatList(localStorage.token, { refreshPinned: true });
			await refreshFolderChatLists();
			toast.success($i18n.t('Chat archived.'));
		} catch (error) {
			console.error('Error archiving chat:', error);
			toast.error($i18n.t('Failed to archive chat.'));
		}
	};

	let showDeleteConfirm = false;

	const confirmWebSearch = async () => {
		const userPrompt = pendingWebSearchPrompt;
		pendingWebSearchPrompt = null;
		webSearchConfirmed = true;

		if (userPrompt !== null) {
			await submitHandler(userPrompt);
		} else {
			webSearchEnabled = true;
		}
	};

	const deleteChatHandler = async (id: string) => {
		showDeleteConfirm = true;
	};

	const confirmDeleteChat = async () => {
		const id = $chatId;
		if (!id) return;

		try {
			const res = await deleteChatById(localStorage.token, id);
			if (res) {
				initNewChat();
				await goto('/');
				await refreshChatList(localStorage.token, { refreshPinned: true });
				allTags.set(await getAllTags(localStorage.token));
				toast.success($i18n.t('Chat deleted.'));
			}
		} catch (error) {
			console.error('Error deleting chat:', error);
			toast.error(`${error}`);
		}
	};
</script>

<svelte:head>
	<title>
		{$settings.showChatTitleInTab !== false && $chatTitle
			? `${$chatTitle.length > 30 ? `${$chatTitle.slice(0, 30)}...` : $chatTitle} / ${$WEBUI_NAME}`
			: `${$WEBUI_NAME}`}
	</title>
</svelte:head>

<audio id="audioElement" style="display: none;"></audio>

{#if getChatVariablesForm(selectedModelIds, chatVariables, $models).conflicts.length > 0}
	<Modal bind:show={showChatVariablesModal} size="md">
		<div>
			<div class="flex justify-between px-4 pt-3 pb-1 dark:text-gray-300">
				<div class="self-center text-sm font-medium">{$i18n.t('Chat Variables')}</div>
				<button
					class="self-center rounded-lg p-1 text-gray-500 transition hover:bg-gray-50 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-200"
					on:click={() => {
						showChatVariablesModal = false;
					}}
				>
					<XMark className="size-4" />
				</button>
			</div>

			<div class="px-5 pb-4 text-sm text-gray-600 dark:text-gray-300">
				<div class="mb-2 text-xs text-gray-500 dark:text-gray-400">
					{$i18n.t('Selected models define incompatible Chat Variables.')}
				</div>
				<div class="flex flex-col gap-1">
					{#each getChatVariablesForm(selectedModelIds, chatVariables, $models).conflicts as conflict}
						<div class="rounded-lg border border-red-200 px-3 py-2 text-xs dark:border-red-900/60">
							<div class="font-medium text-red-600 dark:text-red-400">{conflict.key}</div>
							<div class="mt-1 text-gray-500 dark:text-gray-400">
								{conflict.modelIds.join(', ')}
							</div>
						</div>
					{/each}
				</div>
			</div>
		</div>
	</Modal>
{:else}
	<InputVariablesModal
		bind:show={showChatVariablesModal}
		title={$i18n.t('Chat Variables')}
		variables={getChatVariablesForm(selectedModelIds, chatVariables, $models).variables}
		onSave={saveChatVariables}
	/>
{/if}

<WebSearchConfirmDialog
	bind:show={showWebSearchConfirm}
	title={$i18n.t('Use Web Search?')}
	message={($config?.features?.web_search_confirmation_content ?? '').trim() !== ''
		? ($config?.features?.web_search_confirmation_content ?? '')
		: $i18n.t('Your query will be sent to the configured web search provider.')}
	confirmLabel={$i18n.t('Continue')}
	cancelLabel={$i18n.t('Cancel')}
	on:confirm={confirmWebSearch}
	on:cancel={() => {
		if (pendingWebSearchPrompt === null) {
			webSearchEnabled = false;
		}
		pendingWebSearchPrompt = null;
	}}
/>

<ConversationModeConfirmDialog
	bind:show={showConversationModeConfirmation}
	title={$i18n.t('Start a new conversation?')}
	message={$i18n.t(
		'This conversation mode is fixed. Continue in a new {{MODE}} conversation instead?',
		{ MODE: pendingConversationMode === 'agent' ? 'Agent' : 'Chat' }
	)}
	confirmLabel={$i18n.t('New conversation')}
	cancelLabel={$i18n.t('Cancel')}
	on:confirm={async () => {
		const nextMode = pendingConversationMode;
		pendingConversationMode = null;
		if (nextMode) {
			await goto(`/?mode=${nextMode}`);
		}
	}}
	on:cancel={() => {
		pendingConversationMode = null;
	}}
/>

<DeleteConfirmDialog
	bind:show={showDeleteConfirm}
	title={$i18n.t('Delete chat?')}
	on:confirm={() => {
		confirmDeleteChat();
	}}
>
	<div class=" text-sm text-gray-500 flex-1 line-clamp-3">
		{$i18n.t('This will delete')} <span class="  font-normal">{$chatTitle}</span>.
	</div>
</DeleteConfirmDialog>

<EventConfirmDialog
	bind:show={showEventConfirmation}
	title={eventConfirmationTitle}
	message={eventConfirmationMessage}
	input={eventConfirmationInput}
	inputPlaceholder={eventConfirmationInputPlaceholder}
	inputValue={eventConfirmationInputValue}
	inputType={eventConfirmationInputType}
	inputOptions={eventConfirmationInputOptions}
	on:confirm={(e) => {
		if (eventConfirmationInput) {
			eventCallback(e.detail);
		} else if (e.detail) {
			eventCallback(e.detail);
		} else {
			eventCallback(true);
		}
	}}
	on:cancel={() => {
		eventCallback(false);
	}}
/>

<div
	class="{embedded
		? 'h-full'
		: 'h-screen max-h-[100dvh]'} transition-width duration-200 ease-in-out {$showSidebar &&
	!embedded
		? '  md:max-w-[calc(100%-var(--sidebar-width))]'
		: ' '} w-full max-w-full flex flex-col"
	id={chatContainerId}
>
	{#if !loading}
		<div in:fade={{ duration: 50 }} class="w-full h-full flex flex-col">
			{#if !embedded && $selectedFolder && $selectedFolder?.meta?.background_image_url}
				<div
					class="absolute top-0 left-0 w-full h-full bg-cover bg-center bg-no-repeat"
					style="background-image: url({$selectedFolder?.meta?.background_image_url})  "
				/>

				<div
					class="absolute top-0 left-0 w-full h-full bg-linear-to-t from-white to-white/85 dark:from-gray-900 dark:to-gray-900/90 z-0"
				/>
			{:else if !embedded && ($settings?.backgroundImageUrl ?? $config?.license_metadata?.background_image_url ?? null)}
				<div
					class="absolute top-0 left-0 w-full h-full bg-cover bg-center bg-no-repeat"
					style="background-image: url({$settings?.backgroundImageUrl ??
						$config?.license_metadata?.background_image_url})  "
				/>

				<div
					class="absolute top-0 left-0 w-full h-full bg-linear-to-t from-white to-white/85 dark:from-gray-900 dark:to-gray-900/90 z-0"
				/>
			{/if}

			<PaneGroup direction="horizontal" class="w-full h-full">
				<Pane defaultSize={50} minSize={30} class="h-full flex relative max-w-full flex-col">
					<FilesOverlay show={dragged} />
					{#if embedded}
						<div
							class="h-10 shrink-0 flex items-center justify-between gap-2 border-b border-gray-50/80 px-3 text-gray-700 dark:border-gray-850/40 dark:text-gray-200"
						>
							<div class="flex min-w-0 items-center gap-2">
								<EmbeddedChatHistoryDropdown
									title={embeddedHeaderTitle}
									chats={embeddedChats}
									canCreateNew={!!onNewEmbeddedChat &&
										Object.keys(history?.messages ?? {}).length > 0}
									{loading}
									onNewChat={onNewEmbeddedChat}
									onSelectChat={onSelectEmbeddedChat}
									onDeleteChat={onDeleteEmbeddedChat}
								/>
							</div>
							<Tooltip content={$i18n.t('Close')} placement="bottom">
								<button
									type="button"
									class="rounded-md p-1 text-gray-500 transition hover:text-gray-900 dark:hover:text-white"
									on:click={() => onCloseEmbedded?.()}
									aria-label={$i18n.t('Close')}
								>
									<XMark className="size-4" strokeWidth="2" />
								</button>
							</Tooltip>
						</div>
					{:else}
						<Navbar
							bind:this={navbarElement}
							{readOnly}
							chat={{
								id: $chatId,
								chat: {
									title: $chatTitle,
									mode: conversationMode,
									models: selectedModels,
									system: $settings.system ?? undefined,
									params,
									history,
									timestamp: Date.now()
								}
							}}
							{history}
							title={$chatTitle}
							bind:selectedModels
							{conversationMode}
							{conversationModeLocked}
							{agentModeAvailable}
							onConversationModeSelect={transitionConversationMode}
							onConversationModeCreateNew={(mode) => {
								pendingConversationMode = mode;
								showConversationModeConfirmation = true;
							}}
							shareEnabled={!!history.currentId}
							{initNewChat}
							scrollToTop={!isNearTop ? scrollToTop : null}
							{archiveChatHandler}
							{deleteChatHandler}
							{moveChatHandler}
							onSaveTempChat={async () => {
								try {
									if (!history?.currentId || !Object.keys(history.messages).length) {
										toast.error($i18n.t('No conversation to save'));
										return;
									}
									const messages = createMessagesList(history, history.currentId);
									const title =
										messages.find((m) => m.role === 'user')?.content ?? $i18n.t('New Chat');
									const savedChat = await createNewChat(
										localStorage.token,
										{
											id: uuidv4(),
											title: title.length > 50 ? `${title.slice(0, 50)}...` : title,
											mode: conversationMode,
											models: selectedModels,
											params,
											history,
											messages,
											timestamp: Date.now()
										},
										null,
										chatVariables
									);
									if (savedChat) {
										bindCanonicalModeProfileRevision(savedChat.mode_profile_revision_id);
										temporaryChatEnabled.set(false);
										chatId.set(savedChat.id);
										await refreshChatList(localStorage.token);
										await goto(`/c/${savedChat.id}`);
										toast.success($i18n.t('Conversation saved successfully'));
									}
								} catch (error) {
									console.error('Failed to save temporary chat:', error);
									toast.error($i18n.t('Failed to save conversation'));
								}
							}}
						/>
					{/if}
					<div id="chat-pane" class="flex flex-col flex-auto z-10 w-full @container overflow-auto">
						{#if ($settings?.landingPageMode === 'chat' && !$selectedFolder) || createMessagesList(history, history.currentId).length > 0}
							<div
								class=" pb-2.5 flex flex-col justify-between w-full flex-auto overflow-auto h-0 max-w-full z-10 scrollbar-hidden"
								class:native-auto-follow={autoScroll}
								id="messages-container"
								bind:this={messagesContainerElement}
								on:scroll={() => {
									autoScroll =
										messagesContainerElement.scrollHeight - messagesContainerElement.scrollTop <=
										messagesContainerElement.clientHeight + 5;
									isNearTop = messagesContainerElement.scrollTop <= 100;
								}}
							>
								<div class="min-h-full w-full flex flex-col flex-none">
									<Messages
										bind:this={messagesRef}
										chatId={$chatId}
										{readOnly}
										bind:history
										bind:autoScroll
										bind:prompt
										setInputText={(text) => {
											messageInput?.setText(text);
										}}
										bind:selectedModels
										{atSelectedModel}
										className={embedded ? 'h-full flex pt-4' : 'h-full flex pt-18'}
										{sendMessage}
										{showMessage}
										{submitMessage}
										{continueResponse}
										{regenerateResponse}
										{mergeResponses}
										{chatActionHandler}
										{addMessages}
										allowDelete={!(generating || taskIds?.length)}
										forkHandler={handleForkChat}
										topPadding={!embedded}
										bottomPadding={files.length > 0}
										{onSelect}
										{onInsertToNote}
									/>
								</div>
							</div>

							{#if readOnly}
								<div class="pb-6 z-10">
									<div class="text-xs text-gray-400 dark:text-gray-500 text-center">
										{$i18n.t('Read only')}
									</div>
								</div>
							{:else if agentConversationUnavailable}
								<div class="pb-6 z-10">
									<div class="text-xs text-gray-400 dark:text-gray-500 text-center">
										{$i18n.t('Agent Mode is currently unavailable')}
									</div>
								</div>
							{:else}
								<div
									id={embedded ? messageInputDropzoneId : undefined}
									class=" pb-2 {dragged ? 'z-0' : 'z-10'}"
								>
									<MessageInput
										bind:this={messageInput}
										{history}
										{taskIds}
										bind:selectedModels
										bind:files
										bind:prompt
										bind:autoScroll
										bind:selectedToolIds
										bind:selectedSkillIds
										bind:selectedFilterIds
										bind:imageGenerationEnabled
										bind:codeInterpreterEnabled
										{pendingOAuthTools}
										bind:webSearchEnabled
										bind:reasoningEffort
										bind:atSelectedModel
										bind:showCommands
										bind:dragged
										dropzoneId={messageInputDropzoneId}
										chatId={$chatId}
										{contextUsage}
										{contextCompactionEnabled}
										compactHandler={handleManualCompact}
										statusHandler={handleStatusCommand}
										forkHandler={handleForkChat}
										toolServers={$toolServers}
										{generating}
										{stopResponse}
										{createMessagePair}
										{onUpload}
										messageQueue={$chatRequestQueues[$chatId] ?? []}
										{chatTasks}
										onQueueSendNow={async (id) => {
											const queue = $chatRequestQueues[$chatId] ?? [];
											const item = queue.find((m) => m.id === id);
											if (item) {
												// Remove from queue
												chatRequestQueues.update((q) => ({
													...q,
													[$chatId]: queue.filter((m) => m.id !== id)
												}));
												await stopResponse(false);
												await tick();
												await submitPrompt(item.prompt, item.files);
											}
										}}
										onQueueEdit={(id) => {
											const queue = $chatRequestQueues[$chatId] ?? [];
											const item = queue.find((m) => m.id === id);
											if (item) {
												// Remove from queue
												chatRequestQueues.update((q) => ({
													...q,
													[$chatId]: queue.filter((m) => m.id !== id)
												}));
												// Set files and restore prompt to input
												files = item.files;
												messageInput?.setText(item.prompt);
											}
										}}
										onQueueDelete={(id) => {
											const queue = $chatRequestQueues[$chatId] ?? [];
											chatRequestQueues.update((q) => ({
												...q,
												[$chatId]: queue.filter((m) => m.id !== id)
											}));
										}}
										onChange={(data) => {
											latestModeProfileDraftInput = data;
											if (modeProfileControlsReady) {
												const observation =
													modeProfileCapabilityAuthorityController.observeWithChange(data);
												modeProfileCapabilityAuthority = observation.authority;
												if (observation.changed) modeProfileCapabilityOverrideFields = null;
											}
											if (
												!acceptedSubmitDraftPersistenceSuppressed &&
												shouldAutosaveModeProfileDraft()
											) {
												saveDraft(createModeProfileDraftSnapshot(data), $chatId);
											}
										}}
										onImageGenerationToggle={(enabled) => {
											modeProfileCapabilityAuthority =
												modeProfileCapabilityAuthorityController.markExplicit();
											modeProfileCapabilityOverrideFields = null;
											imageGenerationUserOverride = enabled;
										}}
										onWebSearchToggle={(enabled) => {
											modeProfileCapabilityAuthority =
												modeProfileCapabilityAuthorityController.markExplicit();
											modeProfileCapabilityOverrideFields = null;
											handleWebSearchToggle(enabled);
										}}
										on:submit={async (e) => {
											if (e.detail || files.length > 0) {
												await tick();

												await submitHandler(e.detail);
											}
										}}
									/>

									<div
										class="absolute bottom-1 text-xs text-gray-500 text-center line-clamp-1 right-0 left-0"
									>
										<!-- {$i18n.t('LLMs can make mistakes. Verify important information.')} -->
									</div>
								</div>
							{/if}
						{:else if embedded}
							<div class="flex h-full min-h-0 flex-col justify-end">
								{#if suggestedPrompts.length > 0}
									<div class="flex flex-1 items-end px-5 pb-8">
										<div class="w-full">
											<div class="mb-2 text-[12px] text-gray-300 dark:text-gray-700">
												{$i18n.t('Suggested prompts')}
											</div>
											<div class="flex flex-col">
												{#each suggestedPrompts as suggestion}
													<button
														type="button"
														class="flex min-h-8 w-full items-center justify-between py-1 text-left text-[13px] leading-5 text-gray-500 transition hover:text-gray-700 dark:text-gray-500 dark:hover:text-gray-300"
														on:click={async () => {
															await tick();
															await submitHandler(withSelectedText(suggestion));
														}}
													>
														<span class="min-w-0 truncate">{suggestion}</span>
													</button>
												{/each}
											</div>
										</div>
									</div>
								{/if}
								<div id={embedded ? messageInputDropzoneId : undefined} class="pb-2 z-10">
									<MessageInput
										bind:this={messageInput}
										{history}
										{taskIds}
										bind:selectedModels
										bind:files
										bind:prompt
										bind:autoScroll
										bind:selectedToolIds
										bind:selectedSkillIds
										bind:selectedFilterIds
										bind:imageGenerationEnabled
										bind:codeInterpreterEnabled
										{pendingOAuthTools}
										bind:webSearchEnabled
										bind:atSelectedModel
										bind:showCommands
										bind:dragged
										dropzoneId={messageInputDropzoneId}
										chatId={$chatId}
										{contextUsage}
										{contextCompactionEnabled}
										compactHandler={handleManualCompact}
										statusHandler={handleStatusCommand}
										forkHandler={handleForkChat}
										toolServers={$toolServers}
										{generating}
										{stopResponse}
										{createMessagePair}
										{onUpload}
										messageQueue={$chatRequestQueues[$chatId] ?? []}
										{chatTasks}
										onWebSearchToggle={handleWebSearchToggle}
										on:chatVariables={() => {
											showChatVariablesModal = true;
										}}
										on:submit={async (e) => {
											if (e.detail || files.length > 0) {
												await tick();
												await submitHandler(withSelectedText(e.detail));
											}
										}}
									/>
								</div>
							</div>
						{:else}
							<div class="flex items-center h-full">
								{#if agentConversationUnavailable}
									<div class="w-full text-sm text-gray-400 dark:text-gray-500 text-center">
										{$i18n.t('Agent Mode is currently unavailable')}
									</div>
								{:else}
									<Placeholder
										{history}
										bind:selectedModels
										bind:messageInput
										bind:files
										bind:prompt
										bind:autoScroll
										bind:selectedToolIds
										bind:selectedSkillIds
										bind:selectedFilterIds
										bind:imageGenerationEnabled
										bind:codeInterpreterEnabled
										bind:webSearchEnabled
										bind:reasoningEffort
										bind:atSelectedModel
										bind:showCommands
										bind:dragged
										{pendingOAuthTools}
										toolServers={$toolServers}
										{stopResponse}
										{createMessagePair}
										{onSelect}
										{onUpload}
										onImageGenerationToggle={(enabled) => {
											modeProfileCapabilityAuthority =
												modeProfileCapabilityAuthorityController.markExplicit();
											modeProfileCapabilityOverrideFields = null;
											imageGenerationUserOverride = enabled;
										}}
										onWebSearchToggle={(enabled) => {
											modeProfileCapabilityAuthority =
												modeProfileCapabilityAuthorityController.markExplicit();
											modeProfileCapabilityOverrideFields = null;
											handleWebSearchToggle(enabled);
										}}
										onChange={(data) => {
											latestModeProfileDraftInput = data;
											if (modeProfileControlsReady) {
												const observation =
													modeProfileCapabilityAuthorityController.observeWithChange(data);
												modeProfileCapabilityAuthority = observation.authority;
												if (observation.changed) modeProfileCapabilityOverrideFields = null;
											}
											if (
												!acceptedSubmitDraftPersistenceSuppressed &&
												shouldAutosaveModeProfileDraft()
											) {
												saveDraft(createModeProfileDraftSnapshot(data));
											}
										}}
										on:submit={async (e) => {
											if (e.detail || files.length > 0) {
												await tick();
												await submitHandler(e.detail);
											}
										}}
									/>
								{/if}
							</div>
						{/if}
					</div>
				</Pane>

				{#if !embedded}
					<ChatControls
						bind:this={controlPaneComponent}
						bind:history
						bind:chatFiles
						bind:params
						bind:files
						bind:pane={controlPane}
						chatId={$chatId}
						modelId={selectedModelIds?.at(0) ?? null}
						models={selectedModelIds.reduce((a, e, i, arr) => {
							const model = $models.find((m) => m.id === e);
							if (model) {
								return [...a, model];
							}
							return a;
						}, [])}
						submitPrompt={submitHandler}
						{stopResponse}
						{showMessage}
						{eventTarget}
						{codeInterpreterEnabled}
						containerId={chatContainerId}
					/>
				{/if}
			</PaneGroup>
		</div>
	{:else if loading}
		<div class=" flex items-center justify-center h-full w-full">
			<div class="m-auto">
				<Spinner className="size-5" />
			</div>
		</div>
	{/if}
</div>

<style>
	::-webkit-scrollbar {
		height: 0.5rem;
		width: 0.5rem;
	}
</style>

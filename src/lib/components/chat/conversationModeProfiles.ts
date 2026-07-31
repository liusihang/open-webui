import type {
	ConversationMode,
	ConversationModeProfileDefaults,
	ConversationModeProfilePublic
} from '$lib/apis/configs';

export type ConversationModeProfileSelections = {
	terminalId: string | null;
	toolIds: string[];
	skillIds: string[];
	filterIds: string[];
	featureIds: string[];
};

export type ConversationModeProfileWarning = {
	field: keyof ConversationModeProfileDefaults;
	resourceIds: string[];
};

export type ConversationModeCapabilityChange = {
	selectedToolIds?: unknown;
	selectedSkillIds?: unknown;
	selectedFilterIds?: unknown;
	webSearchEnabled?: unknown;
	codeInterpreterEnabled?: unknown;
	imageGenerationEnabled?: unknown;
};

export type ConversationModeCapabilityAuthority = 'initialized' | 'inherit_bound' | 'explicit';

export const conversationModeCapabilityOverrideFields = [
	'terminal_id',
	'tool_ids',
	'skill_ids',
	'filter_ids',
	'web_search',
	'code_interpreter',
	'image_generation'
] as const;

export type ConversationModeCapabilityOverrideField =
	(typeof conversationModeCapabilityOverrideFields)[number];

type ConversationModeCapabilityAuthorityControllerOptions = {
	existingChat: boolean;
	persistedAuthority?: unknown;
};

const isCapabilityAuthority = (value: unknown): value is ConversationModeCapabilityAuthority =>
	value === 'initialized' || value === 'inherit_bound' || value === 'explicit';

export type ConversationModeDraft = Record<string, unknown> & {
	prompt: string;
	files: unknown[];
};

export const parseConversationModeDraft = (value: string | null): ConversationModeDraft | null => {
	if (typeof value !== 'string') return null;
	try {
		const parsed = JSON.parse(value);
		return parsed &&
			typeof parsed === 'object' &&
			!Array.isArray(parsed) &&
			typeof parsed.prompt === 'string' &&
			Array.isArray(parsed.files)
			? (parsed as ConversationModeDraft)
			: null;
	} catch {
		return null;
	}
};

export type ConversationModeDraftCapabilitySnapshot = {
	authority: Exclude<ConversationModeCapabilityAuthority, 'inherit_bound'>;
	selections: ConversationModeProfileSelections;
	overrideFields?: ConversationModeCapabilityOverrideField[];
};

const isStringArray = (value: unknown): value is string[] =>
	Array.isArray(value) && value.every((item) => typeof item === 'string');

const parseCapabilityOverrideFields = (
	value: unknown
): ConversationModeCapabilityOverrideField[] | null => {
	if (!Array.isArray(value) || value.length === 0) return null;
	const allowed = new Set<string>(conversationModeCapabilityOverrideFields);
	if (!value.every((item) => typeof item === 'string' && allowed.has(item))) return null;
	const normalized = [...new Set(value)] as ConversationModeCapabilityOverrideField[];
	return normalized.length === value.length ? normalized : null;
};

export const getConversationModeDraftCapabilitySnapshot = (
	draft: ConversationModeDraft | null,
	options: { existingChat: boolean }
): ConversationModeDraftCapabilitySnapshot | null => {
	if (!draft) return null;
	const authority = draft.modeProfileCapabilityAuthority;
	const overrideFields =
		draft.modeProfileCapabilityOverrideFields === undefined
			? undefined
			: parseCapabilityOverrideFields(draft.modeProfileCapabilityOverrideFields);
	if (
		draft.modeProfileCapabilitySnapshotVersion !== 1 ||
		(authority !== 'initialized' && authority !== 'explicit') ||
		(options.existingChat && authority !== 'explicit') ||
		!isStringArray(draft.selectedToolIds) ||
		!isStringArray(draft.selectedSkillIds) ||
		!isStringArray(draft.selectedFilterIds) ||
		typeof draft.webSearchEnabled !== 'boolean' ||
		typeof draft.codeInterpreterEnabled !== 'boolean' ||
		typeof draft.imageGenerationEnabled !== 'boolean' ||
		!(typeof draft.selectedTerminalId === 'string' || draft.selectedTerminalId === null) ||
		overrideFields === null
	) {
		return null;
	}

	return {
		authority,
		...(overrideFields ? { overrideFields } : {}),
		selections: {
			terminalId: draft.selectedTerminalId,
			toolIds: [...draft.selectedToolIds],
			skillIds: [...draft.selectedSkillIds],
			filterIds: [...draft.selectedFilterIds],
			featureIds: [
				...(draft.webSearchEnabled ? ['web_search'] : []),
				...(draft.codeInterpreterEnabled ? ['code_interpreter'] : []),
				...(draft.imageGenerationEnabled ? ['image_generation'] : [])
			]
		}
	};
};

export const isConversationModeDraftCompatible = (
	draft: ConversationModeDraft | null,
	mode: ConversationMode
): boolean => draft?.conversationMode === undefined || draft.conversationMode === mode;

export const getConversationModeDraftCapabilitySnapshotForMode = (
	draft: ConversationModeDraft | null,
	mode: ConversationMode,
	options: { existingChat: boolean }
): ConversationModeDraftCapabilitySnapshot | null =>
	draft?.conversationMode === mode
		? getConversationModeDraftCapabilitySnapshot(draft, options)
		: null;

export const getNewConversationModeDraftCapabilityAuthority = (
	draft: ConversationModeDraft | null
): Exclude<ConversationModeCapabilityAuthority, 'inherit_bound'> | null => {
	return (
		getConversationModeDraftCapabilitySnapshot(draft, { existingChat: false })?.authority ?? null
	);
};

const capabilityFingerprint = (value: ConversationModeCapabilityChange) =>
	JSON.stringify({
		toolIds: uniqueStrings(value.selectedToolIds).sort(),
		skillIds: uniqueStrings(value.selectedSkillIds).sort(),
		filterIds: uniqueStrings(value.selectedFilterIds).sort(),
		webSearch: value.webSearchEnabled === true,
		codeInterpreter: value.codeInterpreterEnabled === true,
		imageGeneration: value.imageGenerationEnabled === true
	});

export const createConversationModeCapabilityAuthorityController = (
	options: ConversationModeCapabilityAuthorityControllerOptions
) => {
	let baseline: string | null = null;
	const persistedAuthority =
		isCapabilityAuthority(options.persistedAuthority) &&
		(options.existingChat || options.persistedAuthority !== 'inherit_bound')
			? options.persistedAuthority
			: undefined;
	let authority: ConversationModeCapabilityAuthority = persistedAuthority
		? persistedAuthority
		: options.existingChat
			? 'inherit_bound'
			: 'initialized';

	const observeWithChange = (value: ConversationModeCapabilityChange) => {
		const next = capabilityFingerprint(value);
		const changed = baseline !== null && next !== baseline;
		if (baseline === null) baseline = next;
		else if (changed) authority = 'explicit';
		return { authority, changed };
	};

	return {
		observe: (value: ConversationModeCapabilityChange) => observeWithChange(value).authority,
		observeWithChange,
		rebase: (value: ConversationModeCapabilityChange) => {
			baseline = capabilityFingerprint(value);
			return authority;
		},
		markExplicit: () => {
			authority = 'explicit';
			return authority;
		},
		snapshot: () => authority
	};
};

export const sanitizeConversationModeSelectedToolIds = (
	toolIds: unknown,
	directToolServersPermitted: boolean
) =>
	uniqueStrings(toolIds).filter(
		(id) => directToolServersPermitted || !id.startsWith('direct_server:')
	);

type ConversationModeAvailableToolIdsInput = {
	tools: readonly { id?: unknown; authenticated?: unknown }[];
	toolServers: readonly { info?: unknown }[];
	configuredToolServers?: readonly { config?: { enable?: unknown } }[];
	directToolServerCatalogReady?: boolean;
	directToolServersPermitted: boolean;
};

export const getConversationModeAvailableToolIds = (
	input: ConversationModeAvailableToolIdsInput
) => [
	...new Set([
		...input.tools
			.map((tool) => tool.id)
			.filter((id): id is string => typeof id === 'string' && id.length > 0),
		...(input.directToolServersPermitted
			? (input.directToolServerCatalogReady ?? true)
				? input.toolServers.flatMap((server, index) =>
						server.info ? [`direct_server:${index}`] : []
					)
				: (input.configuredToolServers ?? []).flatMap((server, index) =>
						server.config?.enable === true ? [`direct_server:${index}`] : []
					)
			: [])
	])
];

export type ConversationModePendingOAuthTool = {
	id: string;
	name: string;
	serverId: string;
	authType: string | null;
};

export const partitionConversationModeOAuthTools = (
	selectedToolIds: unknown,
	tools: readonly { id?: unknown; name?: unknown; authenticated?: unknown }[]
): { selectedToolIds: string[]; pendingOAuthTools: ConversationModePendingOAuthTool[] } => {
	const toolsById = new Map(
		tools
			.filter(
				(tool): tool is typeof tool & { id: string } =>
					typeof tool.id === 'string' && tool.id.length > 0
			)
			.map((tool) => [tool.id, tool])
	);
	const selected: string[] = [];
	const pending: ConversationModePendingOAuthTool[] = [];

	for (const id of uniqueStrings(selectedToolIds)) {
		const tool = toolsById.get(id);
		if (tool?.authenticated !== false) {
			selected.push(id);
			continue;
		}

		const parts = id.split(':');
		pending.push({
			id,
			name: typeof tool.name === 'string' && tool.name ? tool.name : id,
			serverId: parts.at(-1) ?? id,
			authType: parts.length > 1 ? (parts[0] === 'server' ? parts[1] : parts[0]) : null
		});
	}

	return { selectedToolIds: selected, pendingOAuthTools: pending };
};

type ConversationModeCapabilityRequestInput = {
	authority: ConversationModeCapabilityAuthority;
	overrideFields?: readonly ConversationModeCapabilityOverrideField[] | null;
	selections: Pick<
		ConversationModeProfileSelections,
		'terminalId' | 'toolIds' | 'skillIds' | 'filterIds'
	>;
	features: Record<string, unknown>;
	directToolServersPermitted: boolean;
	directTerminalIds: readonly string[];
	functionCallingEnabled: boolean;
	terminalEnabled: boolean;
};

export const serializeConversationModeCapabilityRequest = (
	input: ConversationModeCapabilityRequestInput
) => {
	const preservedFeatures = Object.fromEntries(
		Object.entries(input.features).filter(([key]) => key === 'voice' || key === 'memory')
	);
	if (input.authority === 'inherit_bound') {
		return { request: { features: preservedFeatures }, toolServerIds: [], emitToolServers: false };
	}

	const selectedToolIds = sanitizeConversationModeSelectedToolIds(
		input.selections.toolIds,
		input.directToolServersPermitted
	);
	const ownsField = (field: ConversationModeCapabilityOverrideField) =>
		!input.overrideFields || input.overrideFields.includes(field);
	const emitToolServers =
		input.functionCallingEnabled && (ownsField('tool_ids') || ownsField('terminal_id'));
	const toolServerIds =
		emitToolServers && ownsField('tool_ids') && input.directToolServersPermitted
			? selectedToolIds
					.filter((toolId) => toolId.startsWith('direct_server:'))
					.map((toolId) => toolId.slice('direct_server:'.length))
			: [];
	const directTerminalIds = new Set(input.directTerminalIds);
	const selectedTerminalIsDirect =
		input.selections.terminalId !== null &&
		(directTerminalIds.has(input.selections.terminalId) ||
			isConversationModeDirectTerminalId(input.selections.terminalId));
	const terminalId =
		input.terminalEnabled &&
		input.selections.terminalId &&
		(input.directToolServersPermitted || !selectedTerminalIsDirect)
			? input.selections.terminalId
			: null;

	const request: Record<string, unknown> = {};
	if (ownsField('tool_ids')) {
		request.tool_ids = input.functionCallingEnabled
			? selectedToolIds.filter((toolId) => !toolId.startsWith('direct_server:'))
			: [];
	}
	if (ownsField('skill_ids')) {
		request.skill_ids = input.functionCallingEnabled
			? uniqueStrings(input.selections.skillIds)
			: [];
	}
	if (ownsField('filter_ids')) {
		request.filter_ids = input.functionCallingEnabled
			? uniqueStrings(input.selections.filterIds)
			: [];
	}
	if (ownsField('terminal_id')) request.terminal_id = terminalId;
	const requestFeatures: Record<string, unknown> = { ...preservedFeatures };
	for (const featureId of ['web_search', 'code_interpreter', 'image_generation'] as const) {
		if (ownsField(featureId) && Object.prototype.hasOwnProperty.call(input.features, featureId)) {
			requestFeatures[featureId] = input.features[featureId];
		}
	}
	request.features = requestFeatures;

	return {
		request,
		toolServerIds,
		emitToolServers
	};
};

export type ConversationModeToolServer = {
	id?: unknown;
	info?: unknown;
	url?: unknown;
	[key: string]: unknown;
};

export type ConversationModeExternalCatalog = {
	toolServers: ConversationModeToolServer[];
	terminalServers: ConversationModeToolServer[];
};

export type ConversationModeExternalCatalogStatus = 'idle' | 'loading' | 'ready' | 'error';

export type ConversationModeExternalCatalogState = {
	status: ConversationModeExternalCatalogStatus;
	fingerprint: string;
	catalog: ConversationModeExternalCatalog | null;
	error: string | null;
};

type ConversationModeExternalCatalogFingerprintInput = {
	userId: unknown;
	directToolServersPermitted: boolean;
	configuredToolServers: readonly unknown[];
	configuredTerminalServers: readonly unknown[];
	terminalCandidateIds: readonly string[];
};

const stableFingerprintValue = (value: unknown): unknown => {
	if (Array.isArray(value)) return value.map(stableFingerprintValue);
	if (value && typeof value === 'object') {
		return Object.fromEntries(
			Object.entries(value as Record<string, unknown>)
				.sort(([left], [right]) => left.localeCompare(right))
				.map(([key, entry]) => [key, stableFingerprintValue(entry)])
		);
	}
	return value === undefined ? '__undefined__' : value;
};

export const getConversationModeExternalCatalogFingerprint = (
	input: ConversationModeExternalCatalogFingerprintInput
) =>
	JSON.stringify(
		stableFingerprintValue({
			userId: input.userId ?? null,
			directToolServersPermitted: input.directToolServersPermitted,
			configuredToolServers: input.configuredToolServers,
			configuredTerminalServers: input.configuredTerminalServers,
			terminalCandidateIds: [...new Set(input.terminalCandidateIds)].sort()
		})
	);

const copyExternalCatalog = (
	catalog: ConversationModeExternalCatalog | null
): ConversationModeExternalCatalog | null => (catalog ? structuredClone(catalog) : null);

export const createConversationModeExternalCatalogCache = () => {
	const entries = new Map<string, ConversationModeExternalCatalogState>();
	const lastAttemptAt = new Map<string, number>();
	const lastSuccessAt = new Map<string, number>();
	const idle = (fingerprint: string): ConversationModeExternalCatalogState => ({
		status: 'idle',
		fingerprint,
		catalog: null,
		error: null
	});
	const current = (fingerprint: string) => entries.get(fingerprint) ?? idle(fingerprint);
	const snapshot = (fingerprint: string): ConversationModeExternalCatalogState => {
		const state = current(fingerprint);
		return { ...state, catalog: copyExternalCatalog(state.catalog) };
	};

	return {
		snapshot,
		begin: (
			fingerprint: string,
			{ force = false, now = Date.now() }: { force?: boolean; now?: number } = {}
		) => {
			const state = current(fingerprint);
			if (state.status === 'loading') return false;
			if (!force && (state.status === 'ready' || state.status === 'error')) return false;
			lastAttemptAt.set(fingerprint, now);
			entries.set(fingerprint, { ...state, status: 'loading', error: null });
			return true;
		},
		succeed: (
			fingerprint: string,
			catalog: ConversationModeExternalCatalog,
			{ now = Date.now() }: { now?: number } = {}
		) => {
			lastSuccessAt.set(fingerprint, now);
			entries.set(fingerprint, {
				status: 'ready',
				fingerprint,
				catalog: copyExternalCatalog(catalog),
				error: null
			});
			return snapshot(fingerprint);
		},
		fail: (fingerprint: string, error: unknown) => {
			const state = current(fingerprint);
			entries.set(fingerprint, {
				...state,
				status: 'error',
				error: error instanceof Error ? error.message : String(error)
			});
			return snapshot(fingerprint);
		},
		shouldRefresh: (
			fingerprint: string,
			{
				maxAgeMs,
				retryAfterMs,
				now = Date.now()
			}: { maxAgeMs: number; retryAfterMs: number; now?: number }
		) => {
			const state = current(fingerprint);
			if (state.status === 'idle') return true;
			if (state.status === 'loading') return false;
			if (state.status === 'error') {
				return now - (lastAttemptAt.get(fingerprint) ?? 0) >= retryAfterMs;
			}
			return now - (lastSuccessAt.get(fingerprint) ?? 0) >= maxAgeMs;
		}
	};
};

type ConversationModeToolServersInput = {
	emitToolServers: boolean;
	toolServerIds: readonly string[];
	terminalId: unknown;
	directToolServersPermitted: boolean;
	toolServers: readonly ConversationModeToolServer[];
	terminalServers: readonly ConversationModeToolServer[];
};

export const serializeConversationModeToolServers = (
	input: ConversationModeToolServersInput
): { tool_servers?: ConversationModeToolServer[] } => {
	if (!input.emitToolServers) return {};
	if (!input.directToolServersPermitted) return { tool_servers: [] };

	return {
		tool_servers: [
			...input.toolServers.filter(
				(server, index) =>
					input.toolServerIds.includes(String(index)) ||
					input.toolServerIds.includes(String(server.id))
			),
			...input.terminalServers.filter(
				(terminal) => !terminal.id && terminal.url === input.terminalId
			)
		]
	};
};

export const isDirectToolServersPermitted = (
	user:
		| {
				role?: unknown;
				permissions?: { features?: { direct_tool_servers?: unknown } };
		  }
		| null
		| undefined
) => user?.role === 'admin' || user?.permissions?.features?.direct_tool_servers !== false;

type ModelMetadata = {
	terminalId?: unknown;
	toolIds?: unknown;
	skillIds?: unknown;
	defaultFilterIds?: unknown;
	defaultFeatureIds?: unknown;
	capabilities?: Record<string, unknown>;
};

export type ConversationModeProfileModel = {
	id?: string;
	info?: { meta?: unknown };
	filters?: Array<{ id?: string }>;
};

export type ConversationModeProfileAvailability = {
	terminalIds: readonly string[];
	toolIds: readonly string[];
	skillIds: readonly string[];
	filterIds: readonly string[];
	featureIds: readonly string[];
};

export type ConversationModeRequestFeatureState = {
	availableFeatureIds: string[];
	voice: boolean;
	memory: boolean;
	webSearchAlways: boolean;
	imageGenerationUserOverride: boolean | null;
	imageGenerationGloballyEnabled: boolean;
	imageGenerationAllowed: boolean;
};

export type ConversationModeRequestContext<
	TModel extends ConversationModeProfileModel = ConversationModeProfileModel
> = {
	mode: ConversationMode;
	revisionHint: string | null;
	authority: ConversationModeCapabilityAuthority;
	overrideFields: ConversationModeCapabilityOverrideField[] | null;
	profile: ConversationModeProfilePublic | null;
	model: TModel;
	selections: ConversationModeProfileSelections;
	featureState: ConversationModeRequestFeatureState;
	directToolServersPermitted: boolean;
	directTerminalIds: string[];
};

export const captureConversationModeRequestContext = <TModel extends ConversationModeProfileModel>(
	input: ConversationModeRequestContext<TModel>
): ConversationModeRequestContext<TModel> => structuredClone(input);

export type ConversationModeProfileResolutionInput = {
	mode: ConversationMode;
	profile: ConversationModeProfilePublic | null | undefined;
	model: ConversationModeProfileModel | null | undefined;
	available: ConversationModeProfileAvailability;
	currentSelections?: ConversationModeProfileSelections;
	phase: 'initialize' | 'model_change';
};

export type ConversationModeProfileResolution = {
	effective: ConversationModeProfileSelections;
	warnings: ConversationModeProfileWarning[];
	revisionHint: string | null;
};

type ProfileField = keyof ConversationModeProfileDefaults;

const profileFields: ProfileField[] = [
	'terminal_id',
	'tool_ids',
	'skill_ids',
	'filter_ids',
	'feature_ids'
];

const emptySelections = (): ConversationModeProfileSelections => ({
	terminalId: null,
	toolIds: [],
	skillIds: [],
	filterIds: [],
	featureIds: []
});

const copySelections = (
	selections: ConversationModeProfileSelections
): ConversationModeProfileSelections => ({
	terminalId: selections.terminalId,
	toolIds: [...selections.toolIds],
	skillIds: [...selections.skillIds],
	filterIds: [...selections.filterIds],
	featureIds: [...selections.featureIds]
});

const uniqueStrings = (value: unknown): string[] =>
	Array.isArray(value)
		? [
				...new Set(
					value.filter((item): item is string => typeof item === 'string' && item.length > 0)
				)
			]
		: [];

export const filterConversationModeTerminalCandidateIds = (input: {
	candidateIds: readonly string[];
	configuredDirectTerminalIds: readonly string[];
	directToolServersPermitted: boolean;
}): string[] => {
	const configuredDirectTerminalIds = new Set(uniqueStrings(input.configuredDirectTerminalIds));
	return uniqueStrings(input.candidateIds).filter(
		(id) =>
			input.directToolServersPermitted ||
			(!configuredDirectTerminalIds.has(id) && !isConversationModeDirectTerminalId(id))
	);
};

export const isConversationModeDirectTerminalId = (id: unknown): id is string => {
	if (typeof id !== 'string' || id.length === 0) return false;
	try {
		const protocol = new URL(id).protocol;
		return protocol === 'http:' || protocol === 'https:';
	} catch {
		return false;
	}
};

const positiveStringArray = (value: unknown): string[] | null =>
	Array.isArray(value) &&
	value.length > 0 &&
	value.every((item) => typeof item === 'string' && item.length > 0)
		? uniqueStrings(value)
		: null;

export const migrateConversationModeLegacyDraftCapabilities = (
	draft: ConversationModeDraft | null,
	initializedSelections: ConversationModeProfileSelections
): ConversationModeDraftCapabilitySnapshot | null => {
	if (!draft) return null;
	const toolIds = positiveStringArray(draft.selectedToolIds);
	const skillIds = positiveStringArray(draft.selectedSkillIds);
	const filterIds = positiveStringArray(draft.selectedFilterIds);
	const terminalId =
		typeof draft.selectedTerminalId === 'string' && draft.selectedTerminalId.length > 0
			? draft.selectedTerminalId
			: null;
	const positiveFeatureIds = [
		...(draft.webSearchEnabled === true ? ['web_search'] : []),
		...(draft.codeInterpreterEnabled === true ? ['code_interpreter'] : []),
		...(draft.imageGenerationEnabled === true ? ['image_generation'] : [])
	];
	if (!toolIds && !skillIds && !filterIds && !terminalId && positiveFeatureIds.length === 0) {
		return null;
	}

	const initialized = copySelections(initializedSelections);
	return {
		authority: 'explicit',
		overrideFields: [
			...(terminalId ? (['terminal_id'] as const) : []),
			...(toolIds ? (['tool_ids'] as const) : []),
			...(skillIds ? (['skill_ids'] as const) : []),
			...(filterIds ? (['filter_ids'] as const) : []),
			...(draft.webSearchEnabled === true ? (['web_search'] as const) : []),
			...(draft.codeInterpreterEnabled === true ? (['code_interpreter'] as const) : []),
			...(draft.imageGenerationEnabled === true ? (['image_generation'] as const) : [])
		],
		selections: {
			terminalId: terminalId ?? initialized.terminalId,
			toolIds: toolIds ?? initialized.toolIds,
			skillIds: skillIds ?? initialized.skillIds,
			filterIds: filterIds ?? initialized.filterIds,
			featureIds: [...new Set([...initialized.featureIds, ...positiveFeatureIds])]
		}
	};
};

const metadata = (model: ConversationModeProfileModel | null | undefined): ModelMetadata =>
	(model?.info?.meta as ModelMetadata | undefined) ?? {};

const modelDefaults = (
	model: ConversationModeProfileModel | null | undefined
): ConversationModeProfileSelections => {
	const meta = metadata(model);
	return {
		terminalId: typeof meta.terminalId === 'string' ? meta.terminalId : null,
		toolIds: uniqueStrings(meta.toolIds),
		skillIds: uniqueStrings(meta.skillIds),
		filterIds: uniqueStrings(meta.defaultFilterIds),
		featureIds: uniqueStrings(meta.defaultFeatureIds)
	};
};

export const getConversationModeRequestFeatures = (
	context: ConversationModeRequestContext,
	selections: ConversationModeProfileSelections
): Record<string, boolean> => {
	const meta = metadata(context.model);
	const availableFeatures = new Set(context.featureState.availableFeatureIds);
	const featureEnabled = (featureId: string) => selections.featureIds.includes(featureId);
	const canUseImageGeneration =
		context.featureState.imageGenerationGloballyEnabled &&
		context.featureState.imageGenerationAllowed &&
		meta.capabilities?.image_generation === true;
	const imageGeneration = canUseImageGeneration
		? typeof context.featureState.imageGenerationUserOverride === 'boolean'
			? context.featureState.imageGenerationUserOverride
			: featureEnabled('image_generation') ||
				uniqueStrings(meta.defaultFeatureIds).includes('image_generation')
		: false;
	const features: Record<string, boolean> = {
		voice: context.featureState.voice,
		image_generation: imageGeneration,
		code_interpreter:
			availableFeatures.has('code_interpreter') && featureEnabled('code_interpreter'),
		web_search:
			availableFeatures.has('web_search') &&
			(featureEnabled('web_search') ||
				(!context.revisionHint &&
					(meta.capabilities?.web_search ?? true) &&
					context.featureState.webSearchAlways))
	};
	if (context.featureState.memory) features.memory = true;
	return features;
};

const profileValue = <T>(
	profile: ConversationModeProfilePublic | null | undefined,
	field: ProfileField,
	fallback: T
): T => {
	const defaults = profile?.defaults;
	if (!defaults || !Object.prototype.hasOwnProperty.call(defaults, field)) return fallback;
	const value = defaults[field];
	return value === 'inherit' || value === undefined ? fallback : (value as T);
};

const supportsTerminal = (model: ConversationModeProfileModel | null | undefined) =>
	metadata(model).capabilities?.function_calling !== false &&
	metadata(model).capabilities?.terminal !== false;

const supportsFeature = (model: ConversationModeProfileModel | null | undefined, id: string) =>
	metadata(model).capabilities?.[id] !== false;

const supportsFunctionCalling = (model: ConversationModeProfileModel | null | undefined) =>
	metadata(model).capabilities?.function_calling !== false;

const supportsFilter = (model: ConversationModeProfileModel | null | undefined, id: string) => {
	if (!Array.isArray(model?.filters)) return true;
	return model.filters.some((filter) => filter?.id === id);
};

const filterSelection = (
	field: ProfileField,
	ids: string[],
	availableIds: readonly string[],
	supports: (id: string) => boolean
) => {
	const available = new Set(availableIds);
	const accepted = ids.filter((id) => available.has(id) && supports(id));
	return {
		accepted,
		warning:
			accepted.length === ids.length
				? null
				: { field, resourceIds: ids.filter((id) => !accepted.includes(id)) }
	};
};

export const resolveConversationModeProfile = (
	input: ConversationModeProfileResolutionInput
): ConversationModeProfileResolution => {
	const defaults = modelDefaults(input.model);
	const requested =
		input.phase === 'model_change'
			? copySelections(input.currentSelections ?? emptySelections())
			: {
					terminalId: profileValue(input.profile, 'terminal_id', defaults.terminalId),
					toolIds: uniqueStrings(profileValue(input.profile, 'tool_ids', defaults.toolIds)),
					skillIds: uniqueStrings(profileValue(input.profile, 'skill_ids', defaults.skillIds)),
					filterIds: uniqueStrings(profileValue(input.profile, 'filter_ids', defaults.filterIds)),
					featureIds: uniqueStrings(profileValue(input.profile, 'feature_ids', defaults.featureIds))
				};
	const warnings: ConversationModeProfileWarning[] = [];

	const terminal = filterSelection(
		'terminal_id',
		requested.terminalId ? [requested.terminalId] : [],
		input.available.terminalIds,
		() => supportsTerminal(input.model)
	);
	if (terminal.warning) warnings.push(terminal.warning);
	const tools = filterSelection('tool_ids', requested.toolIds, input.available.toolIds, () =>
		supportsFunctionCalling(input.model)
	);
	if (tools.warning) warnings.push(tools.warning);
	const skills = filterSelection('skill_ids', requested.skillIds, input.available.skillIds, () =>
		supportsFunctionCalling(input.model)
	);
	if (skills.warning) warnings.push(skills.warning);
	const filters = filterSelection(
		'filter_ids',
		requested.filterIds,
		input.available.filterIds,
		(id) => supportsFunctionCalling(input.model) && supportsFilter(input.model, id)
	);
	if (filters.warning) warnings.push(filters.warning);
	const features = filterSelection(
		'feature_ids',
		requested.featureIds,
		input.available.featureIds,
		(id) => supportsFeature(input.model, id)
	);
	if (features.warning) warnings.push(features.warning);
	const featureIds = terminal.accepted.length
		? features.accepted.filter((id) => id !== 'code_interpreter')
		: features.accepted;
	if (featureIds.length !== features.accepted.length) {
		warnings.push({ field: 'feature_ids', resourceIds: ['code_interpreter'] });
	}

	return {
		effective: {
			terminalId: terminal.accepted[0] ?? null,
			toolIds: tools.accepted,
			skillIds: skills.accepted,
			filterIds: filters.accepted,
			featureIds
		},
		warnings,
		revisionHint: input.profile?.mode === input.mode ? input.profile.current_revision_id : null
	};
};

type ConversationModeRequestCapabilitiesInput = Omit<
	ConversationModeProfileResolutionInput,
	'phase'
> & {
	authority: ConversationModeCapabilityAuthority;
};

export const resolveConversationModeRequestCapabilities = (
	input: ConversationModeRequestCapabilitiesInput
): ConversationModeProfileResolution => {
	const { authority, ...resolutionInput } = input;
	if (authority === 'inherit_bound') {
		return {
			effective: copySelections(input.currentSelections ?? emptySelections()),
			warnings: [],
			revisionHint: null
		};
	}

	return resolveConversationModeProfile({
		...resolutionInput,
		phase: 'model_change'
	});
};

export type ConversationModeProfileDraftSnapshot = ConversationModeProfileResolution & {
	applied: boolean;
};

export const createConversationModeProfileDraftController = () => {
	let initializedDraftId: string | null = null;
	let snapshot: ConversationModeProfileDraftSnapshot = {
		effective: emptySelections(),
		warnings: [],
		revisionHint: null,
		applied: false
	};

	const current = () => ({
		...snapshot,
		effective: copySelections(snapshot.effective),
		warnings: snapshot.warnings.map((warning) => ({
			...warning,
			resourceIds: [...warning.resourceIds]
		}))
	});

	return {
		initialize: (draftId: string, resolution: ConversationModeProfileResolution) => {
			if (initializedDraftId === draftId) return { ...current(), applied: false };
			initializedDraftId = draftId;
			snapshot = { ...resolution, effective: copySelections(resolution.effective), applied: true };
			return current();
		},
		applyModelChange: (resolution: ConversationModeProfileResolution) => {
			snapshot = {
				...resolution,
				revisionHint: snapshot.revisionHint,
				effective: copySelections(resolution.effective),
				applied: false
			};
			return current();
		},
		bindCanonicalRevision: (revisionHint: string | null | undefined) => {
			if (typeof revisionHint === 'string' && revisionHint) snapshot.revisionHint = revisionHint;
			return current();
		},
		hydrateRevisionHint: (revisionHint: string | null | undefined) => {
			if (typeof revisionHint === 'string' && revisionHint) snapshot.revisionHint = revisionHint;
			return current();
		},
		snapshot: current
	};
};

export const controlledProfileFields = profileFields;

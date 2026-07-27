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

type ConversationModeCapabilityChange = {
	selectedToolIds?: unknown;
	selectedSkillIds?: unknown;
	selectedFilterIds?: unknown;
	webSearchEnabled?: unknown;
	codeInterpreterEnabled?: unknown;
	imageGenerationEnabled?: unknown;
};

export type ConversationModeCapabilityAuthority = 'initialized' | 'inherit_bound' | 'explicit';

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
};

const isStringArray = (value: unknown): value is string[] =>
	Array.isArray(value) && value.every((item) => typeof item === 'string');

export const getConversationModeDraftCapabilitySnapshot = (
	draft: ConversationModeDraft | null,
	options: { existingChat: boolean }
): ConversationModeDraftCapabilitySnapshot | null => {
	if (!draft) return null;
	const authority = draft.modeProfileCapabilityAuthority;
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
		!(typeof draft.selectedTerminalId === 'string' || draft.selectedTerminalId === null)
	) {
		return null;
	}

	return {
		authority,
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

	return {
		observe: (value: ConversationModeCapabilityChange) => {
			const next = capabilityFingerprint(value);
			if (baseline === null) baseline = next;
			else if (next !== baseline) authority = 'explicit';
			return authority;
		},
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
			.filter((tool) => tool.authenticated !== false)
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

type ConversationModeCapabilityRequestInput = {
	authority: ConversationModeCapabilityAuthority;
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
	const emitToolServers = input.functionCallingEnabled;
	const toolServerIds =
		emitToolServers && input.directToolServersPermitted
			? selectedToolIds
					.filter((toolId) => toolId.startsWith('direct_server:'))
					.map((toolId) => toolId.slice('direct_server:'.length))
			: [];
	const directTerminalIds = new Set(input.directTerminalIds);
	const terminalId =
		input.terminalEnabled &&
		input.selections.terminalId &&
		(input.directToolServersPermitted || !directTerminalIds.has(input.selections.terminalId))
			? input.selections.terminalId
			: null;

	return {
		request: {
			tool_ids: input.functionCallingEnabled
				? selectedToolIds.filter((toolId) => !toolId.startsWith('direct_server:'))
				: [],
			skill_ids: input.functionCallingEnabled ? uniqueStrings(input.selections.skillIds) : [],
			filter_ids: input.functionCallingEnabled ? uniqueStrings(input.selections.filterIds) : [],
			terminal_id: terminalId,
			features: input.features
		},
		toolServerIds,
		emitToolServers
	};
};

type ConversationModeToolServer = {
	id?: unknown;
	url?: unknown;
	[key: string]: unknown;
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

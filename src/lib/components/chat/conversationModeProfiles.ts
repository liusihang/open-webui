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
	metadata(model).capabilities?.terminal !== false;

const supportsFeature = (model: ConversationModeProfileModel | null | undefined, id: string) =>
	metadata(model).capabilities?.[id] === true;

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
	const tools = filterSelection('tool_ids', requested.toolIds, input.available.toolIds, () => true);
	if (tools.warning) warnings.push(tools.warning);
	const skills = filterSelection(
		'skill_ids',
		requested.skillIds,
		input.available.skillIds,
		() => true
	);
	if (skills.warning) warnings.push(skills.warning);
	const filters = filterSelection(
		'filter_ids',
		requested.filterIds,
		input.available.filterIds,
		(id) => supportsFilter(input.model, id)
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
		snapshot: current
	};
};

export const controlledProfileFields = profileFields;

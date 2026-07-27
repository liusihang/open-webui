import type {
	ConversationMode,
	ConversationModeProfileContent,
	ConversationModeProfileDefaults,
	ConversationModeProfileHistory,
	ConversationModeProfileRevision,
	ConversationModeProfileValidationIssue,
	ConversationModeProfileWarning
} from '$lib/apis/configs';
import { ModeProfileApiError } from '$lib/apis/configs';

export type TriState = 'inherit' | 'disabled' | 'override';
export type ModeProfileOperation = 'save' | 'restore' | 'detail' | 'history';

type ScalarDefault = { state: TriState; value: string };
type CollectionDefault = { state: TriState; ids: string[] };

export type ConversationModeProfileDraft = {
	schemaVersion: number;
	systemPrompt: string;
	terminal: ScalarDefault;
	tools: CollectionDefault;
	skills: CollectionDefault;
	filters: CollectionDefault;
	features: CollectionDefault;
};

export type ModeProfileRequest = {
	id: number;
	mode: ConversationMode;
	operation: ModeProfileOperation;
	revisionId: string;
	draft: ConversationModeProfileDraft | null;
	draftVersion: number;
};

export type ModeProfileState = {
	profile: ConversationModeProfileRevision | null;
	history: ConversationModeProfileHistory | null;
	detail: ConversationModeProfileRevision | null;
	draft: ConversationModeProfileDraft | null;
	dirty: boolean;
	draftVersion: number;
	loading: Record<ModeProfileOperation, boolean>;
	warnings: ConversationModeProfileWarning[];
	validationIssues: ConversationModeProfileValidationIssue[];
	conflict: string | null;
	error: string;
};

export type CatalogItem = {
	id: string;
	name: string;
	active: boolean;
	disabled: boolean;
	label: string;
};

const defaultOperations = (): Record<ModeProfileOperation, boolean> => ({
	save: false,
	restore: false,
	detail: false,
	history: false
});

const emptyState = (): ModeProfileState => ({
	profile: null,
	history: null,
	detail: null,
	draft: null,
	dirty: false,
	draftVersion: 0,
	loading: defaultOperations(),
	warnings: [],
	validationIssues: [],
	conflict: null,
	error: ''
});

const hasOwn = <Key extends PropertyKey>(value: object, key: Key) =>
	Object.prototype.hasOwnProperty.call(value, key);

const scalarDefault = (
	defaults: Partial<ConversationModeProfileDefaults>,
	key: 'terminal_id'
): ScalarDefault => {
	if (!hasOwn(defaults, key) || defaults[key] === 'inherit') return { state: 'inherit', value: '' };
	if (defaults[key] === null) return { state: 'disabled', value: '' };
	return { state: 'override', value: String(defaults[key]) };
};

const collectionDefault = (
	defaults: Partial<ConversationModeProfileDefaults>,
	key: 'tool_ids' | 'skill_ids' | 'filter_ids' | 'feature_ids'
): CollectionDefault => {
	if (!hasOwn(defaults, key) || defaults[key] === 'inherit') return { state: 'inherit', ids: [] };
	const value = defaults[key];
	if (!Array.isArray(value) || value.length === 0) return { state: 'disabled', ids: [] };
	return { state: 'override', ids: [...value] };
};

export const decodeDefaults = (
	defaults: Partial<ConversationModeProfileDefaults>
): ConversationModeProfileDraft => ({
	schemaVersion: 1,
	systemPrompt: '',
	terminal: scalarDefault(defaults, 'terminal_id'),
	tools: collectionDefault(defaults, 'tool_ids'),
	skills: collectionDefault(defaults, 'skill_ids'),
	filters: collectionDefault(defaults, 'filter_ids'),
	features: collectionDefault(defaults, 'feature_ids')
});

export const draftFromRevision = (
	revision: ConversationModeProfileRevision
): ConversationModeProfileDraft => ({
	...decodeDefaults(revision.defaults),
	schemaVersion: revision.schema_version,
	systemPrompt: revision.system_prompt
});

const cloneDraft = (draft: ConversationModeProfileDraft | null) =>
	draft && {
		...draft,
		terminal: { ...draft.terminal },
		tools: { ...draft.tools, ids: [...draft.tools.ids] },
		skills: { ...draft.skills, ids: [...draft.skills.ids] },
		filters: { ...draft.filters, ids: [...draft.filters.ids] },
		features: { ...draft.features, ids: [...draft.features.ids] }
	};

const collectionContent = (value: CollectionDefault) =>
	value.state === 'inherit' ? 'inherit' : value.state === 'disabled' ? [] : [...value.ids];

export const contentFromDraft = (
	draft: ConversationModeProfileDraft
): ConversationModeProfileContent => ({
	schema_version: draft.schemaVersion,
	system_prompt: draft.systemPrompt,
	defaults: {
		terminal_id:
			draft.terminal.state === 'inherit'
				? 'inherit'
				: draft.terminal.state === 'disabled'
					? null
					: draft.terminal.value,
		tool_ids: collectionContent(draft.tools),
		skill_ids: collectionContent(draft.skills),
		filter_ids: collectionContent(draft.filters),
		feature_ids: collectionContent(draft.features)
	}
});

const featureLabel = (id: string) =>
	({
		web_search: 'Web Search',
		code_interpreter: 'Code Interpreter',
		image_generation: 'Image Generation'
	})[id] ?? id;

const summary = (label: string, value: ScalarDefault | CollectionDefault, ids = false) => {
	if (value.state === 'inherit') return `${label}: Inherit`;
	if (value.state === 'disabled') return `${label}: Disabled`;
	const selected = ids ? (value as CollectionDefault).ids : [(value as ScalarDefault).value];
	return `${label}: Override (${selected.map(featureLabel).join(', ') || 'No selections'})`;
};

export const detailPresentation = (revision: ConversationModeProfileRevision) => {
	const draft = draftFromRevision(revision);
	return {
		systemPrompt: revision.system_prompt,
		defaults: [
			summary('Terminal', draft.terminal),
			summary('Tools', draft.tools, true),
			summary('Skills', draft.skills, true),
			summary('Filters', draft.filters, true),
			summary('Feature defaults', draft.features, true)
		],
		metadata: [
			`Revision: ${revision.revision_number} (${revision.revision_id})`,
			`Created: ${new Date(revision.created_at * 1000).toLocaleString()}`,
			`Content hash: ${revision.content_hash ? `${revision.content_hash.slice(0, 12)}…` : 'Not provided by server'}`,
			`Restored from: ${revision.restored_from_revision_id ?? 'Not restored'}`
		]
	};
};

const findProfileError = (error: unknown): string | null => {
	if (typeof error === 'string' && error.trim()) return error;
	if (error instanceof Error && error.message) return error.message;
	if (error && typeof error === 'object') {
		const value = error as Record<string, unknown>;
		for (const key of ['reason', 'message', 'detail', 'error']) {
			const normalized = findProfileError(value[key]);
			if (normalized) return normalized;
		}
	}
	return null;
};

export const normalizeProfileError = (error: unknown) =>
	findProfileError(error) ?? 'The profile service is unavailable. Please retry.';

export const catalogItems = (value: unknown, selectedIds: string[] = []): CatalogItem[] => {
	const values = Array.isArray(value)
		? value
		: Object.values((value ?? {}) as Record<string, unknown>);
	const catalog = values.flatMap((entry) => {
		if (!entry || typeof entry !== 'object' || !('id' in entry)) return [];
		const item = entry as Record<string, unknown>;
		const id = String(item.id);
		const active = item.is_active !== false && item.isActive !== false;
		if (!active && !selectedIds.includes(id)) return [];
		const name = typeof item.name === 'string' && item.name ? item.name : id;
		return [{ id, name, active, disabled: !active, label: active ? name : `${name} (inactive)` }];
	});
	const catalogIds = new Set(catalog.map((item) => item.id));
	const unavailable = [...new Set(selectedIds)]
		.filter((id) => !catalogIds.has(id))
		.map((id) => ({
			id,
			name: id,
			active: false,
			disabled: true,
			label: `${id} (Unavailable)`
		}));
	return [...catalog, ...unavailable];
};

export const modeForTabKey = (mode: ConversationMode, key: string): ConversationMode | null => {
	if (key === 'Home') return 'chat';
	if (key === 'End') return 'agent';
	if (key === 'ArrowLeft' || key === 'ArrowRight') return mode === 'chat' ? 'agent' : 'chat';
	return null;
};

export const createConversationModeProfileController = () => {
	const states: Record<ConversationMode, ModeProfileState> = {
		chat: emptyState(),
		agent: emptyState()
	};
	const latest: Record<ConversationMode, Partial<Record<ModeProfileOperation, number>>> = {
		chat: {},
		agent: {}
	};
	let sequence = 0;

	const state = (mode: ConversationMode) => states[mode];

	const clearFeedback = (mode: ConversationMode) => {
		const current = state(mode);
		current.error = '';
		current.conflict = null;
		current.validationIssues = [];
	};

	const applyRevision = (
		mode: ConversationMode,
		revision: ConversationModeProfileRevision,
		options: { preserveDraft?: boolean } = {}
	) => {
		const current = state(mode);
		const preserveDraft = options.preserveDraft ?? current.dirty;
		current.profile = revision;
		current.warnings = revision.warnings;
		if (!preserveDraft || !current.draft) {
			current.draft = draftFromRevision(revision);
			current.dirty = false;
			current.draftVersion += 1;
		}
	};

	const updateDraft = (
		mode: ConversationMode,
		updater: (draft: ConversationModeProfileDraft) => ConversationModeProfileDraft
	) => {
		const current = state(mode);
		if (!current.draft) return;
		current.draft = updater(cloneDraft(current.draft) as ConversationModeProfileDraft);
		current.dirty = true;
		current.draftVersion += 1;
	};

	const canBegin = (mode: ConversationMode, operation: ModeProfileOperation) => {
		const loading = state(mode).loading;
		if (operation === 'save' || operation === 'restore') return !loading.save && !loading.restore;
		return !loading[operation];
	};

	const begin = (
		mode: ConversationMode,
		operation: ModeProfileOperation
	): ModeProfileRequest | null => {
		if (!canBegin(mode, operation)) return null;
		const current = state(mode);
		if (operation === 'save' || operation === 'restore') clearFeedback(mode);
		current.loading[operation] = true;
		const request = {
			id: ++sequence,
			mode,
			operation,
			revisionId: current.profile?.revision_id ?? '',
			draft: cloneDraft(current.draft),
			draftVersion: current.draftVersion
		};
		latest[mode][operation] = request.id;
		return request;
	};

	const accepts = (request: ModeProfileRequest) =>
		latest[request.mode][request.operation] === request.id;

	const complete = (request: ModeProfileRequest, apply: (current: ModeProfileState) => void) => {
		if (!accepts(request)) return false;
		const current = state(request.mode);
		current.loading[request.operation] = false;
		apply(current);
		return true;
	};

	const completeSave = (request: ModeProfileRequest, revision: ConversationModeProfileRevision) =>
		complete(request, (current) => {
			current.profile = revision;
			current.warnings = revision.warnings;
			if (current.draftVersion === request.draftVersion) {
				current.draft = draftFromRevision(revision);
				current.dirty = false;
				current.draftVersion += 1;
			}
		});

	const completeHistory = (request: ModeProfileRequest, history: ConversationModeProfileHistory) =>
		complete(request, (current) => {
			current.history = history;
		});

	const completeDetail = (request: ModeProfileRequest, detail: ConversationModeProfileRevision) =>
		complete(request, (current) => {
			current.detail = detail;
		});

	const fail = (request: ModeProfileRequest, error: unknown) =>
		complete(request, (current) => {
			current.error = normalizeProfileError(error);
		});

	return {
		state,
		clearFeedback,
		clearDetail: (mode: ConversationMode) => {
			state(mode).detail = null;
		},
		applyRevision,
		updateDraft,
		canBegin,
		begin,
		accepts,
		completeSave,
		completeHistory,
		completeDetail,
		fail
	};
};

type ConversationModeProfileController = ReturnType<typeof createConversationModeProfileController>;

const isRecord = (value: unknown): value is Record<string, unknown> =>
	!!value && typeof value === 'object' && !Array.isArray(value);

const conflictMessage = (detail: Record<string, unknown>) => {
	const current = isRecord(detail.current_revision) ? detail.current_revision : null;
	const revisionNumber = current?.revision_number;
	const revisionId = current?.revision_id;
	if (typeof revisionNumber === 'number' && typeof revisionId === 'string' && revisionId) {
		return `Current revision is ${revisionNumber} (${revisionId}).`;
	}
	return 'A newer current revision is available.';
};

export const setProfileOperationFailure = async ({
	controller,
	request,
	error,
	refresh
}: {
	controller: ConversationModeProfileController;
	request: ModeProfileRequest;
	error: unknown;
	refresh: (mode: ConversationMode) => Promise<void>;
}): Promise<void> => {
	if (!controller.accepts(request)) return;
	const state = controller.state(request.mode);
	if (!(error instanceof ModeProfileApiError)) {
		controller.fail(request, error);
		return;
	}

	const detail: unknown = error.detail;
	controller.fail(request, detail);
	const objectDetail = isRecord(detail) ? detail : null;
	if (error.status === 409 && objectDetail?.code === 'mode_profile_revision_conflict') {
		state.conflict = conflictMessage(objectDetail);
		state.error =
			'This profile changed while you were editing. Current metadata was refreshed; your draft is preserved.';
		try {
			await refresh(request.mode);
		} catch (refreshError) {
			if (!controller.accepts(request)) return;
			state.error = `${state.error} Refresh failed: ${normalizeProfileError(refreshError)}`;
		}
		return;
	}

	state.validationIssues =
		objectDetail && Array.isArray(objectDetail.issues)
			? (objectDetail.issues as ConversationModeProfileValidationIssue[])
			: [];
	state.error =
		error.status >= 500
			? 'The profile service could not complete this request. No private prompt content was exposed.'
			: normalizeProfileError(detail);
};

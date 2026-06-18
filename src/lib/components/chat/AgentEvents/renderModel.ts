import type {
	AgentRunEventMetadata,
	AgentRunEventPayload,
	AgentRunEventState,
	AgentRunEventViewItem,
	AgentRunState
} from './types';

export type AgentRunTransportStatus = 'loading' | 'live' | 'reconnecting' | 'error';

export type AgentRunRenderStatus =
	| 'queued'
	| 'running'
	| 'waiting'
	| 'done'
	| 'error'
	| 'cancelled';

export type AgentRunDetailSectionKind = 'input' | 'output' | 'error' | 'debug';

export type AgentRunDetailSection = {
	id: string;
	kind: AgentRunDetailSectionKind;
	title: string;
	value: unknown;
};

export type AgentRunRenderGroupKind =
	| 'run'
	| 'step'
	| 'tool'
	| 'approval'
	| 'artifact'
	| 'subagent'
	| 'model'
	| 'fallback';

export type AgentRunArtifactPart = {
	id: string;
	groupId: string;
	name: string;
	path: string | null;
	mimeType: string | null;
	size: string | null;
	status: AgentRunRenderStatus;
};

export type AgentRunFinalPart = {
	id: string;
	title: string;
	content: string;
	status: AgentRunRenderStatus;
};

export type AgentRunErrorPart = {
	id: string;
	groupId: string;
	title: string;
	value: unknown;
};

export type AgentRunRenderGroup = {
	id: string;
	kind: AgentRunRenderGroupKind;
	status: AgentRunRenderStatus;
	title: string;
	subtitle: string | null;
	metadata: AgentRunEventMetadata[];
	seqRange: {
		start: number;
		end: number;
	};
	events: AgentRunEventViewItem[];
	detailSections: AgentRunDetailSection[];
};

export type AgentRunRenderModel = {
	runStatus: AgentRunState;
	transportStatus: AgentRunTransportStatus;
	counts: AgentRunEventState['counts'];
	groups: AgentRunRenderGroup[];
	artifacts: AgentRunArtifactPart[];
	finalAnswer: AgentRunFinalPart | null;
	errors: AgentRunErrorPart[];
};

type RenderOptions = {
	transportStatus: AgentRunTransportStatus;
};

type GroupDraft = {
	id: string;
	kind: AgentRunRenderGroupKind;
	events: AgentRunEventViewItem[];
};

export const createAgentRunRenderModel = (
	state: AgentRunEventState,
	options: RenderOptions
): AgentRunRenderModel => {
	const groupDrafts = new Map<string, GroupDraft>();

	for (const item of state.items) {
		if (item.category === 'final') {
			continue;
		}

		const key = getGroupKey(item);
		const existing = groupDrafts.get(key);
		if (existing) {
			existing.events.push(item);
		} else {
			groupDrafts.set(key, {
				id: key,
				kind: getGroupKind(item),
				events: [item]
			});
		}
	}

	const groups = [...groupDrafts.values()]
		.map(toRenderGroup)
		.sort((a, b) => a.seqRange.start - b.seqRange.start);
	const artifacts = groups
		.filter((group) => group.kind === 'artifact')
		.map(toArtifactPart)
		.filter((artifact): artifact is AgentRunArtifactPart => artifact !== null);
	const errors = groups.flatMap((group) =>
		group.detailSections
			.filter((section) => section.kind === 'error')
			.map((section) => ({
				id: `${group.id}:error:${section.id}`,
				groupId: group.id,
				title: group.title,
				value: section.value
			}))
	);

	return {
		runStatus: state.runStatus,
		transportStatus: options.transportStatus,
		counts: state.counts,
		groups,
		artifacts,
		finalAnswer: createFinalAnswer(state),
		errors
	};
};

const getGroupKind = (item: AgentRunEventViewItem): AgentRunRenderGroupKind => {
	if (item.eventType === 'action.summary') {
		return 'step';
	}

	if (item.category === 'action') {
		return 'fallback';
	}

	return item.category;
};

const getGroupKey = (item: AgentRunEventViewItem): string => {
	const details = getDetails(item);

	if (item.category === 'tool') {
		return scopedKey(
			'tool',
			firstString(details.tool_call_id, details.call_id),
			item,
			firstString(details.tool_name, details.name, details.tool)
		);
	}

	if (item.category === 'approval') {
		return scopedKey(
			'approval',
			firstString(details.approval_id, details.id),
			item,
			firstString(details.action, details.description)
		);
	}

	if (item.category === 'subagent') {
		return `subagent:${item.participantId ?? firstString(details.participant_id) ?? item.seq}`;
	}

	if (item.category === 'model') {
		return scopedKey(
			'model',
			firstString(details.selection_id, details.request_id),
			item,
			'selection'
		);
	}

	if (item.category === 'artifact') {
		return `artifact:${firstString(details.artifact_id, details.id, details.path, details.name) ?? item.seq}`;
	}

	if (item.eventType === 'action.summary') {
		return `step:${item.seq}`;
	}

	if (item.category === 'run') {
		return `run:${item.seq}`;
	}

	return `${item.category}:${item.eventType}:${item.seq}`;
};

const scopedKey = (
	prefix: string,
	stableId: string | null,
	item: AgentRunEventViewItem,
	fallbackName: string | null
) => {
	if (stableId) {
		return `${prefix}:${stableId}`;
	}

	const window = Math.floor((item.seq - 1) / 4);
	return `${prefix}:${fallbackName ?? 'unknown'}:${item.participantId ?? 'agent'}:${window}`;
};

const toRenderGroup = (draft: GroupDraft): AgentRunRenderGroup => {
	const events = [...draft.events].sort((a, b) => a.seq - b.seq);
	const first = events[0];
	const last = events[events.length - 1];

	return {
		id: draft.id,
		kind: draft.kind,
		status: getGroupStatus(draft.kind, events),
		title: getGroupTitle(draft.kind, events),
		subtitle: getGroupSubtitle(draft.kind, events),
		metadata: getGroupMetadata(draft.kind, events),
		seqRange: {
			start: first.seq,
			end: last.seq
		},
		events,
		detailSections: getDetailSections(draft.kind, events)
	};
};

const getGroupStatus = (
	kind: AgentRunRenderGroupKind,
	events: AgentRunEventViewItem[]
): AgentRunRenderStatus => {
	if (events.some((event) => event.status === 'error')) {
		return 'error';
	}

	const last = events[events.length - 1];
	if (last.eventType === 'run.cancelled') {
		return 'cancelled';
	}

	if (kind === 'approval' && !events.some((event) => event.eventType === 'approval.completed')) {
		return 'waiting';
	}

	if (
		(kind === 'tool' && events.some((event) => event.eventType === 'tool.completed')) ||
		(kind === 'approval' && events.some((event) => event.eventType === 'approval.completed')) ||
		(kind === 'subagent' && events.some((event) => event.eventType === 'subagent.completed')) ||
		(kind === 'model' && events.some((event) => event.eventType === 'model.selection.completed'))
	) {
		return 'done';
	}

	if (events.some((event) => event.status === 'running')) {
		return 'running';
	}

	return 'done';
};

const getGroupTitle = (kind: AgentRunRenderGroupKind, events: AgentRunEventViewItem[]): string => {
	const last = events[events.length - 1];
	const merged = mergeDetails(events);

	switch (kind) {
		case 'tool':
			return firstString(merged.tool_name, merged.name, merged.tool) ?? last.summary;
		case 'approval':
			return events.some((event) => event.eventType === 'approval.completed')
				? 'Approval completed'
				: 'Approval requested';
		case 'artifact':
			return firstString(merged.name, merged.path, merged.artifact_id) ?? 'Artifact';
		case 'subagent':
			return (
				firstString(merged.participant_name, merged.name, last.participantId) ??
				last.summary ??
				'Subagent'
			);
		case 'model':
			return firstString(merged.model_id, merged.model, merged.name) ?? last.summary;
		case 'step':
		case 'fallback':
		case 'run':
		default:
			return last.summary;
	}
};

const getGroupSubtitle = (
	kind: AgentRunRenderGroupKind,
	events: AgentRunEventViewItem[]
): string | null => {
	const last = events[events.length - 1];
	const merged = mergeDetails(events);

	switch (kind) {
		case 'tool':
			return firstString(merged.summary, merged.description);
		case 'approval':
			return firstString(merged.action, merged.description, merged.request);
		case 'artifact':
			return firstString(merged.path, merged.mime_type);
		case 'subagent':
			return firstString(merged.result_summary, merged.status, last.participantId);
		case 'model':
			return firstString(merged.provider, merged.reason);
		case 'fallback':
		case 'step':
		case 'run':
		default:
			return null;
	}
};

const getGroupMetadata = (
	kind: AgentRunRenderGroupKind,
	events: AgentRunEventViewItem[]
): AgentRunEventMetadata[] => {
	const metadata = new Map<string, string>();
	const merged = mergeDetails(events);

	for (const event of events) {
		for (const item of event.metadata) {
			if (isRedundantMetadata(kind, item.label)) {
				continue;
			}
			metadata.set(item.label, item.value);
		}
	}

	if (kind === 'tool') {
		addMetadata(metadata, 'Call', firstString(merged.tool_call_id, merged.call_id));
	}

	if (kind === 'subagent') {
		addMetadata(metadata, 'Model', firstString(merged.model_id, merged.model));
	}

	if (kind === 'artifact') {
		addMetadata(metadata, 'Type', firstString(merged.mime_type));
		addMetadata(metadata, 'Size', firstString(merged.size));
	}

	return [...metadata].map(([label, value]) => ({ label, value }));
};

const isRedundantMetadata = (kind: AgentRunRenderGroupKind, label: string) => {
	if (kind === 'approval' && label === 'Action') {
		return true;
	}
	if (kind === 'artifact' && label === 'Path') {
		return true;
	}
	if (kind === 'model' && label === 'Provider') {
		return true;
	}

	return false;
};

const getDetailSections = (
	kind: AgentRunRenderGroupKind,
	events: AgentRunEventViewItem[]
): AgentRunDetailSection[] => {
	const merged = mergeDetails(events);
	const sections: AgentRunDetailSection[] = [];

	if (kind === 'tool') {
		addSection(
			sections,
			'input',
			'Input',
			pickFields(merged, ['arguments', 'query', 'path', 'command'])
		);
		addSection(
			sections,
			'output',
			'Output',
			pickFields(merged, ['result', 'content', 'summary', 'process_refs', 'warnings'])
		);
		addSection(
			sections,
			'error',
			'Error',
			pickFields(merged, ['structured_error', 'error', 'message', 'code'])
		);
		addDebugSection(sections, merged);
		return sections;
	}

	if (kind === 'approval') {
		addSection(
			sections,
			'input',
			'Request',
			pickFields(merged, ['action', 'description', 'request'])
		);
		addSection(
			sections,
			'output',
			'Decision',
			pickFields(merged, ['status', 'decision', 'approved'])
		);
		addDebugSection(sections, merged);
		return sections;
	}

	if (kind === 'artifact') {
		addSection(
			sections,
			'output',
			'Artifact',
			pickFields(merged, ['artifact_id', 'name', 'path', 'mime_type', 'size'])
		);
		addDebugSection(sections, merged);
		return sections;
	}

	if (kind === 'subagent') {
		addSection(
			sections,
			'output',
			'Result',
			pickFields(merged, ['result_summary', 'status', 'model_id', 'model'])
		);
		addSection(
			sections,
			'error',
			'Error',
			pickFields(merged, ['structured_error', 'error', 'message', 'code'])
		);
		addDebugSection(sections, merged);
		return sections;
	}

	if (kind === 'model') {
		addSection(
			sections,
			'output',
			'Selection',
			pickFields(merged, ['model_id', 'model', 'provider', 'reason'])
		);
		addDebugSection(sections, merged);
		return sections;
	}

	if (Object.keys(merged).length > 0) {
		addSection(sections, kind === 'fallback' ? 'debug' : 'output', 'Details', merged);
	}

	return sections;
};

const addDebugSection = (sections: AgentRunDetailSection[], details: AgentRunEventPayload) => {
	const visibleDetails = omitFields(details, [
		'arguments',
		'query',
		'path',
		'command',
		'result',
		'content',
		'summary',
		'process_refs',
		'warnings',
		'structured_error',
		'error',
		'message',
		'code',
		'action',
		'description',
		'request',
		'status',
		'decision',
		'approved',
		'artifact_id',
		'name',
		'mime_type',
		'size',
		'result_summary',
		'model_id',
		'model',
		'provider',
		'reason',
		'tool_call_id',
		'call_id',
		'tool_name',
		'tool',
		'approval_id',
		'id',
		'participant_id',
		'selection_id',
		'request_id'
	]);

	addSection(sections, 'debug', 'Debug', visibleDetails);
};

const addSection = (
	sections: AgentRunDetailSection[],
	kind: AgentRunDetailSectionKind,
	title: string,
	value: AgentRunEventPayload
) => {
	if (Object.keys(value).length === 0) {
		return;
	}

	sections.push({
		id: `${kind}-${sections.length + 1}`,
		kind,
		title,
		value
	});
};

const createFinalAnswer = (state: AgentRunEventState): AgentRunFinalPart | null => {
	if (!state.finalText) {
		return null;
	}

	return {
		id: 'final-answer',
		title: 'Final answer',
		content: state.finalText,
		status: getFinalStatus(state.runStatus)
	};
};

const getFinalStatus = (runStatus: AgentRunState): AgentRunRenderStatus => {
	if (runStatus === 'completed') {
		return 'done';
	}

	if (runStatus === 'failed' || runStatus === 'budget_exceeded') {
		return 'error';
	}

	if (runStatus === 'cancelled') {
		return 'cancelled';
	}

	return 'running';
};

const toArtifactPart = (group: AgentRunRenderGroup): AgentRunArtifactPart | null => {
	const details = mergeDetails(group.events);
	const id = firstString(details.artifact_id, details.id, details.path, details.name);
	const name = firstString(details.name, details.path, details.artifact_id);

	if (!id || !name) {
		return null;
	}

	return {
		id,
		groupId: group.id,
		name,
		path: firstString(details.path),
		mimeType: firstString(details.mime_type),
		size: firstString(details.size),
		status: group.status
	};
};

const mergeDetails = (events: AgentRunEventViewItem[]): AgentRunEventPayload => {
	return events.reduce<AgentRunEventPayload>((merged, event) => {
		if (!event.details) {
			return merged;
		}

		return {
			...merged,
			...event.details
		};
	}, {});
};

const getDetails = (item: AgentRunEventViewItem): AgentRunEventPayload =>
	item.details && isPlainObject(item.details) ? item.details : {};

const pickFields = (value: AgentRunEventPayload, keys: string[]): AgentRunEventPayload => {
	const picked: AgentRunEventPayload = {};

	for (const key of keys) {
		if (value[key] !== undefined && value[key] !== null) {
			picked[key] = value[key];
		}
	}

	return picked;
};

const omitFields = (value: AgentRunEventPayload, keys: string[]): AgentRunEventPayload => {
	const omitted = new Set(keys);
	const next: AgentRunEventPayload = {};

	for (const [key, fieldValue] of Object.entries(value)) {
		if (!omitted.has(key) && fieldValue !== undefined && fieldValue !== null) {
			next[key] = fieldValue;
		}
	}

	return next;
};

const addMetadata = (metadata: Map<string, string>, label: string, value: string | null) => {
	if (value) {
		metadata.set(label, value);
	}
};

const firstString = (...values: unknown[]): string | null => {
	for (const value of values) {
		if (typeof value === 'string' && value.trim()) {
			return value;
		}

		if (typeof value === 'number' && Number.isFinite(value)) {
			return `${value}`;
		}

		if (typeof value === 'boolean') {
			return value ? 'true' : 'false';
		}
	}

	return null;
};

const isPlainObject = (value: unknown): value is Record<string, unknown> => {
	return value !== null && typeof value === 'object' && !Array.isArray(value);
};

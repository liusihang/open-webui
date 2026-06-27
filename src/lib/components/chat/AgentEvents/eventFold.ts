import type {
	AgentRunEventCategory,
	AgentRunEventMetadata,
	AgentRunEvent,
	AgentRunEventPayload,
	AgentRunEventState,
	AgentRunEventType,
	AgentRunEventViewItem,
	AgentTextBlockKind
} from './types';

const STRIPPED_DETAIL_KEYS = new Set([
	'chain_of_thought',
	'private',
	'raw',
	'raw_reasoning',
	'reasoning',
	'thought'
]);

const AGENT_RUN_EVENT_CATEGORIES: AgentRunEventCategory[] = [
	'run',
	'action',
	'tool',
	'approval',
	'artifact',
	'subagent',
	'model',
	'final'
];

const createEmptyCounts = (): AgentRunEventState['counts'] =>
	Object.fromEntries(
		AGENT_RUN_EVENT_CATEGORIES.map((category) => [category, 0])
	) as AgentRunEventState['counts'];

export const createAgentRunEventState = (): AgentRunEventState => ({
	items: [],
	lastSeq: 0,
	runStatus: 'queued',
	runStatusSeq: 0,
	counts: createEmptyCounts(),
	finalText: '',
	finalStarted: false,
	seenSeqs: new Set(),
	seenFinalDeltaKeys: new Set(),
	finalDeltaChunks: new Map(),
	textBlocks: [],
	seenTextDeltaKeys: new Set(),
	textDeltaChunks: new Map()
});

export const foldAgentRunEvents = (events: AgentRunEvent[]): AgentRunEventState => {
	return [...events]
		.sort((a, b) => a.seq - b.seq)
		.reduce((state, event) => foldAgentRunEvent(state, event), createAgentRunEventState());
};

export const foldAgentRunEvent = (
	state: AgentRunEventState,
	event: AgentRunEvent
): AgentRunEventState => {
	if (!Number.isFinite(event.seq) || event.seq <= 0 || state.seenSeqs.has(event.seq)) {
		return state;
	}

	const nextState: AgentRunEventState = {
		items: [...state.items],
		lastSeq: Math.max(state.lastSeq, event.seq),
		runStatus: state.runStatus,
		runStatusSeq: state.runStatusSeq,
		counts: { ...state.counts },
		finalText: state.finalText,
		finalStarted: state.finalStarted || event.event_type === 'final.started',
		seenSeqs: new Set(state.seenSeqs).add(event.seq),
		seenFinalDeltaKeys: new Set(state.seenFinalDeltaKeys),
		finalDeltaChunks: new Map(state.finalDeltaChunks),
		textBlocks: [...state.textBlocks],
		seenTextDeltaKeys: new Set(state.seenTextDeltaKeys),
		textDeltaChunks: new Map(state.textDeltaChunks)
	};

	const nextRunStatus = getRunStatusForEvent(event.event_type);
	if (nextRunStatus && event.seq >= nextState.runStatusSeq) {
		nextState.runStatus = nextRunStatus;
		nextState.runStatusSeq = event.seq;
	}

	if (event.event_type === 'final.delta') {
		if (!isFinalDeltaRenderable(nextState, event)) {
			return nextState;
		}

		const deltaKey = getFinalDeltaKey(event);
		if (!nextState.seenFinalDeltaKeys.has(deltaKey)) {
			nextState.seenFinalDeltaKeys.add(deltaKey);
			nextState.finalDeltaChunks.set(deltaKey, {
				streamId: getFinalStreamId(event),
				deltaIndex: getDeltaIndex(event),
				text: getFinalDeltaText(event),
				seq: event.seq
			});
			nextState.finalText = buildFinalText(nextState.finalDeltaChunks);
		}

		upsertFinalDeltaItem(nextState, event);
		return nextState;
	}

	if (event.event_type === 'text.delta') {
		const blockId = getTextStreamId(event);
		if (!blockId) {
			return nextState;
		}

		const deltaKey = getTextDeltaKey(event, blockId);
		if (deltaKey && nextState.seenTextDeltaKeys.has(deltaKey)) {
			return nextState;
		}

		if (deltaKey) {
			nextState.seenTextDeltaKeys.add(deltaKey);
		}

		const kind = resolveTextBlockKind(event);
		const deltaText = getTextDeltaText(event);
		nextState.textDeltaChunks.set(`${blockId}:${getDeltaIndex(event)}`, {
			blockId,
			deltaIndex: getDeltaIndex(event),
			text: deltaText,
			seq: event.seq,
			kind,
			participantId: event.participant_id ?? null,
			phase: event.phase ?? null,
			createdAt: event.created_at
		});
		nextState.textBlocks = rebuildTextBlocks(nextState.textDeltaChunks);

		return nextState;
	}

	insertViewItem(nextState, toViewItem(event));
	return nextState;
};

const upsertFinalDeltaItem = (state: AgentRunEventState, event: AgentRunEvent) => {
	const existingIndex = state.items.findIndex((item) => item.eventType === 'final.delta');
	const item = toViewItem({
		...event,
		summary: event.summary?.trim() || 'Streaming final answer'
	});

	if (existingIndex >= 0) {
		state.items[existingIndex] = {
			...state.items[existingIndex],
			seq: Math.max(state.items[existingIndex].seq, event.seq),
			summary: item.summary,
			status: item.status,
			createdAt: event.created_at
		};
		state.items.sort((a, b) => a.seq - b.seq);
		return;
	}

	insertViewItem(state, item);
};

const insertViewItem = (state: AgentRunEventState, item: AgentRunEventViewItem) => {
	state.counts[item.category] += 1;

	const nextIndex = state.items.findIndex((existing) => existing.seq > item.seq);
	if (nextIndex === -1) {
		state.items.push(item);
		return;
	}

	state.items.splice(nextIndex, 0, item);
};

const toViewItem = (event: AgentRunEvent): AgentRunEventViewItem => ({
	seq: event.seq,
	eventType: event.event_type,
	category: getEventCategory(event.event_type),
	label: getEventLabel(event.event_type),
	participantId: event.participant_id ?? null,
	phase: event.phase ?? null,
	summary: getEventSummary(event),
	metadata: getEventMetadata(event),
	details: sanitizeDetails(event.payload),
	status: getEventStatus(event.event_type),
	createdAt: event.created_at
});

const getEventSummary = (event: AgentRunEvent): string => {
	const summary = event.summary?.trim();
	if (summary) {
		return summary;
	}

	const toolName = getString(event.payload.tool_name ?? event.payload.name ?? event.payload.tool);
	const participantName = getString(
		event.payload.participant_name ?? event.payload.participant_id ?? event.participant_id
	);
	const artifactName = getString(
		event.payload.name ?? event.payload.path ?? event.payload.artifact_id
	);
	const modelName = getString(event.payload.model_id ?? event.payload.model ?? event.payload.name);

	switch (event.event_type) {
		case 'run.queued':
			return 'Agent queued';
		case 'run.running':
			return 'Agent started';
		case 'tool.requested':
			return toolName ? `Requested ${toolName}` : 'Tool requested';
		case 'tool.started':
			return toolName ? `Running ${toolName}` : 'Tool running';
		case 'tool.completed':
			return toolName ? `Completed ${toolName}` : 'Tool completed';
		case 'tool.failed':
			return toolName ? `${toolName} failed` : 'Tool failed';
		case 'approval.requested':
			return 'Approval requested';
		case 'approval.completed':
			return 'Approval completed';
		case 'artifact.registered':
			return artifactName ? `Registered ${artifactName}` : 'Artifact registered';
		case 'subagent.created':
			return participantName ? `Started ${participantName}` : 'Subagent started';
		case 'subagent.updated':
			return participantName ? `Updated ${participantName}` : 'Subagent updated';
		case 'subagent.completed':
			return participantName ? `Completed ${participantName}` : 'Subagent completed';
		case 'subagent.failed':
			return participantName ? `${participantName} failed` : 'Subagent failed';
		case 'model.selection.requested':
			return 'Selecting model';
		case 'model.selection.completed':
			return modelName ? `Selected ${modelName}` : 'Model selected';
		case 'final.started':
			return 'Writing final answer';
		case 'final.delta':
			return 'Streaming final answer';
		case 'run.completed':
			return 'Agent completed';
		case 'run.failed':
			return 'Agent failed';
		case 'run.cancelled':
			return 'Agent cancelled';
		case 'run.budget_exceeded':
			return 'Agent budget exceeded';
		case 'action.summary':
		default:
			return 'Agent update';
	}
};

const getRunStatusForEvent = (
	eventType: AgentRunEventType
): AgentRunEventState['runStatus'] | null => {
	switch (eventType) {
		case 'run.queued':
			return 'queued';
		case 'run.running':
			return 'running';
		case 'action.summary':
		case 'tool.requested':
		case 'tool.started':
		case 'subagent.created':
		case 'subagent.updated':
		case 'model.selection.requested':
			return 'running';
		case 'approval.requested':
			return 'waiting_approval';
		case 'approval.completed':
			return 'running';
		case 'final.started':
		case 'final.delta':
			return 'finalizing';
		case 'run.completed':
			return 'completed';
		case 'run.failed':
			return 'failed';
		case 'run.cancelled':
			return 'cancelled';
		case 'run.budget_exceeded':
			return 'budget_exceeded';
		default:
			return null;
	}
};

const getEventCategory = (eventType: AgentRunEventType): AgentRunEventCategory => {
	if (eventType.startsWith('tool.')) {
		return 'tool';
	}
	if (eventType.startsWith('approval.')) {
		return 'approval';
	}
	if (eventType.startsWith('artifact.')) {
		return 'artifact';
	}
	if (eventType.startsWith('subagent.')) {
		return 'subagent';
	}
	if (eventType.startsWith('model.')) {
		return 'model';
	}
	if (eventType.startsWith('final.')) {
		return 'final';
	}
	if (eventType.startsWith('run.')) {
		return 'run';
	}

	return 'action';
};

const getEventLabel = (eventType: AgentRunEventType): string => {
	switch (getEventCategory(eventType)) {
		case 'tool':
			return 'Tool';
		case 'approval':
			return 'Approval';
		case 'artifact':
			return 'Artifact';
		case 'subagent':
			return 'Subagent';
		case 'model':
			return 'Model';
		case 'final':
			return 'Final answer';
		case 'run':
			return 'Run';
		case 'action':
		default:
			return 'Update';
	}
};

const getEventMetadata = (event: AgentRunEvent): AgentRunEventMetadata[] => {
	const metadata: AgentRunEventMetadata[] = [];
	const category = getEventCategory(event.event_type);
	const payload = event.payload;

	if (category === 'tool') {
		addMetadata(metadata, 'Status', payload.status);
		addMetadata(metadata, 'Process', getFirstProcessId(payload.process_refs));
		return metadata;
	}

	if (category === 'approval') {
		addMetadata(metadata, 'Action', payload.action ?? payload.description);
		addMetadata(metadata, 'Approval', payload.status);
		return metadata;
	}

	if (category === 'artifact') {
		addMetadata(metadata, 'Path', payload.path);
		addMetadata(metadata, 'Type', payload.mime_type);
		return metadata;
	}

	if (category === 'subagent') {
		addMetadata(metadata, 'Status', payload.status);
		addMetadata(metadata, 'Model', payload.model_id ?? payload.model);
		return metadata;
	}

	if (category === 'model') {
		addMetadata(metadata, 'Provider', payload.provider);
		addMetadata(metadata, 'Reason', payload.reason);
		return metadata;
	}

	if (category === 'run' || category === 'final') {
		addMetadata(metadata, 'Phase', event.phase);
		return metadata;
	}

	addMetadata(metadata, 'Status', payload.status);
	return metadata;
};

const addMetadata = (metadata: AgentRunEventMetadata[], label: string, value: unknown) => {
	const stringValue = getString(value);
	if (stringValue) {
		metadata.push({ label, value: stringValue });
	}
};

const getFirstProcessId = (value: unknown): string | null => {
	if (!Array.isArray(value)) {
		return null;
	}

	const first = value[0];
	if (!isPlainObject(first)) {
		return null;
	}

	return getString(first.process_id ?? first.pid ?? first.id);
};

const getEventStatus = (eventType: AgentRunEventType): AgentRunEventViewItem['status'] => {
	if (
		eventType === 'tool.failed' ||
		eventType === 'subagent.failed' ||
		eventType === 'run.failed' ||
		eventType === 'run.budget_exceeded'
	) {
		return 'error';
	}

	if (
		eventType === 'run.running' ||
		eventType === 'action.summary' ||
		eventType === 'tool.requested' ||
		eventType === 'tool.started' ||
		eventType === 'approval.requested' ||
		eventType === 'subagent.created' ||
		eventType === 'subagent.updated' ||
		eventType === 'model.selection.requested' ||
		eventType === 'final.started' ||
		eventType === 'final.delta'
	) {
		return 'running';
	}

	return 'done';
};

const sanitizeDetails = (payload: AgentRunEventPayload): AgentRunEventPayload | null => {
	const sanitized = sanitizeValue(payload);

	if (!isPlainObject(sanitized) || Object.keys(sanitized).length === 0) {
		return null;
	}

	return sanitized;
};

const sanitizeValue = (value: unknown): unknown => {
	if (Array.isArray(value)) {
		return value.map((item) => sanitizeValue(item));
	}

	if (!isPlainObject(value)) {
		return value;
	}

	const sanitized: Record<string, unknown> = {};
	for (const [key, nestedValue] of Object.entries(value)) {
		if (STRIPPED_DETAIL_KEYS.has(key.toLowerCase())) {
			continue;
		}

		sanitized[key] = sanitizeValue(nestedValue);
	}

	return sanitized;
};

const isFinalDeltaRenderable = (state: AgentRunEventState, event: AgentRunEvent): boolean => {
	return state.finalStarted || event.phase === 'finalizing';
};

const getFinalDeltaText = (event: AgentRunEvent): string => {
	return getString(event.payload.delta ?? event.payload.text ?? event.summary) ?? '';
};

const getTextDeltaText = (event: AgentRunEvent): string => {
	return getString(event.payload.delta ?? event.payload.text ?? event.summary) ?? '';
};

const getFinalDeltaKey = (event: AgentRunEvent): string => {
	const streamId = getFinalStreamId(event);
	const deltaIndex = getDeltaIndex(event);
	return `${streamId}:${deltaIndex}`;
};

const getTextDeltaKey = (event: AgentRunEvent, blockId: string): string => {
	return `${blockId}:${getDeltaIndex(event)}`;
};

const getFinalStreamId = (event: AgentRunEvent): string => {
	return getString(event.payload.final_stream_id) ?? 'default';
};

const getTextStreamId = (event: AgentRunEvent): string | null => {
	return getString(event.payload.block_id ?? event.payload.blockId);
};

const getDeltaIndex = (event: AgentRunEvent): number => {
	const value = event.payload.delta_index;
	if (typeof value === 'number' && Number.isFinite(value)) {
		return value;
	}

	if (typeof value === 'string') {
		const parsed = Number(value);
		if (Number.isFinite(parsed)) {
			return parsed;
		}
	}

	return event.seq;
};

const TEXT_BLOCK_KINDS = new Set<AgentTextBlockKind>(['assistant_note', 'action_summary']);

const resolveTextBlockKind = (event: AgentRunEvent): AgentTextBlockKind => {
	const raw = event.payload.block_kind ?? event.payload.blockKind;
	const value = typeof raw === 'string' ? raw.toLowerCase() : '';
	return TEXT_BLOCK_KINDS.has(value as AgentTextBlockKind)
		? (value as AgentTextBlockKind)
		: 'legacy';
};

const buildFinalText = (chunks: AgentRunEventState['finalDeltaChunks']): string => {
	return [...chunks.values()]
		.sort((a, b) => {
			if (a.seq === b.seq) {
				if (a.streamId === b.streamId) {
					return a.deltaIndex - b.deltaIndex;
				}

				return a.streamId.localeCompare(b.streamId);
			}

			return a.seq - b.seq;
		})
		.map((chunk) => chunk.text)
		.join('');
};

const rebuildTextBlocks = (
	chunks: AgentRunEventState['textDeltaChunks']
): AgentRunEventState['textBlocks'] => {
	const byBlock = new Map<
		string,
		{
			blockId: string;
			kind: AgentTextBlockKind;
			participantId: string | null;
			phase: string | null;
			chunks: { text: string; deltaIndex: number; seq: number; createdAt: number }[];
		}
	>();

	for (const chunk of chunks.values()) {
		const existing = byBlock.get(chunk.blockId);
		if (!existing) {
			byBlock.set(chunk.blockId, {
				blockId: chunk.blockId,
				kind: chunk.kind,
				participantId: chunk.participantId,
				phase: chunk.phase,
				chunks: [
					{
						text: chunk.text,
						deltaIndex: chunk.deltaIndex,
						seq: chunk.seq,
						createdAt: chunk.createdAt
					}
				]
			});
			continue;
		}

		existing.chunks.push({
			text: chunk.text,
			deltaIndex: chunk.deltaIndex,
			seq: chunk.seq,
			createdAt: chunk.createdAt
		});
	}

	return [...byBlock.values()]
		.map((entry) => {
			const ordered = [...entry.chunks].sort((a, b) => {
				if (a.deltaIndex === b.deltaIndex) {
					return a.seq - b.seq;
				}
				return a.deltaIndex - b.deltaIndex;
			});
			const first = ordered[0];
			const last = ordered[ordered.length - 1];
			return {
				id: entry.blockId,
				kind: entry.kind,
				participantId: entry.participantId,
				phase: entry.phase,
				text: ordered.map((chunk) => chunk.text).join(''),
				status: 'running' as const,
				firstSeq: first.seq,
				lastSeq: last.seq,
				createdAt: first.createdAt
			};
		})
		.sort((a, b) => {
			if (a.firstSeq === b.firstSeq) {
				return a.id.localeCompare(b.id);
			}
			return a.firstSeq - b.firstSeq;
		});
};

const getString = (value: unknown): string | null => {
	if (typeof value === 'string') {
		return value;
	}

	if (typeof value === 'number') {
		return `${value}`;
	}

	return null;
};

const isPlainObject = (value: unknown): value is Record<string, unknown> => {
	return value !== null && typeof value === 'object' && !Array.isArray(value);
};

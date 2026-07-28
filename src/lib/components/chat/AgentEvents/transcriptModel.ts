import type {
	AgentConnectionState,
	AgentRunEventMetadata,
	AgentRunEventPayload,
	AgentRunEventState,
	AgentRunEventViewItem,
	AgentRunState,
	AgentTextBlockKind,
	AgentTranscriptApprovalPart,
	AgentTranscriptArtifactPart,
	AgentTranscriptErrorPart,
	AgentTranscriptModel,
	AgentTranscriptModelPart,
	AgentTranscriptSubagentPart,
	AgentTranscriptTextPart,
	AgentTranscriptToolPart,
	AgentTranscriptUserInputPart
} from './types';

const TERMINAL_RUN_STATUSES: ReadonlySet<AgentRunState> = new Set([
	'completed',
	'failed',
	'cancelled',
	'budget_exceeded'
]);

/**
 * Builds the render model consumed by `AgentTranscript.svelte`.
 *
 * Contract:
 *  - `finalText` from the folded state becomes `final.content`; never reads
 *    text.delta chunks into the final answer.
 *  - `textBlocks` become transcript text parts (assistant_note / action_summary
 *    / legacy_note).
 *  - Tool/approval/artifact/subagent events are grouped by their stable id
 *    (`tool_call_id` / `approval_id` / `artifact_id` / `participant_id`).
 *  - Raw payload is taken from the already-sanitized `items[].details`; this
 *    module must not re-ingest unsanitized payload.
 */
export const buildAgentTranscriptModel = (
	state: AgentRunEventState,
	connectionState: AgentConnectionState = 'connected'
): AgentTranscriptModel => {
	const parts = buildParts(state);
	const timing = summarizeTiming(state);

	return {
		runStatus: state.runStatus,
		connectionState,
		isRunning:
			state.runStatus === 'running' ||
			state.runStatus === 'waiting_approval' ||
			state.runStatus === 'waiting_user_input' ||
			state.runStatus === 'finalizing',
		isTerminal: TERMINAL_RUN_STATUSES.has(state.runStatus),
		...timing,
		parts,
		final: state.finalText
			? { content: state.finalText, done: TERMINAL_RUN_STATUSES.has(state.runStatus) }
			: null,
		summary: summarizeParts(parts)
	};
};

const summarizeTiming = (state: AgentRunEventState) => {
	const timestamps = [
		...state.items.map((item) => item.createdAt),
		...state.textBlocks.map((block) => block.createdAt)
	]
		.map(timestampToMs)
		.filter((value): value is number => value !== null);

	if (timestamps.length === 0) {
		return {
			startedAt: null,
			updatedAt: null,
			elapsedMs: null
		};
	}

	const startedAt = Math.min(...timestamps);
	const updatedAt = TERMINAL_RUN_STATUSES.has(state.runStatus)
		? Math.max(...timestamps)
		: Date.now();

	return {
		startedAt,
		updatedAt,
		elapsedMs: Math.max(0, updatedAt - startedAt)
	};
};

const timestampToMs = (value: number | null | undefined): number | null => {
	if (
		!Number.isFinite(value ?? Number.NaN) ||
		value === null ||
		value === undefined ||
		value <= 0
	) {
		return null;
	}
	if (value > 1_000_000_000_000_000) {
		return Math.round(value / 1_000_000);
	}
	if (value > 1_000_000_000_000) {
		return Math.round(value);
	}
	return Math.round(value * 1000);
};

const summarizeParts = (parts: AgentTranscriptModelPart[]) => {
	let toolCount = 0;
	let artifactCount = 0;
	let approvalCount = 0;
	let userInputCount = 0;
	let subagentCount = 0;
	let hasError = false;
	let hasPendingApproval = false;
	let hasPendingUserInput = false;

	for (const part of parts) {
		switch (part.kind) {
			case 'tool':
				toolCount += 1;
				if (part.status === 'error') {
					hasError = true;
				}
				break;
			case 'approval':
				approvalCount += 1;
				if (part.status === 'pending') {
					hasPendingApproval = true;
				}
				break;
			case 'user_input':
				userInputCount += 1;
				if (part.status === 'pending') {
					hasPendingUserInput = true;
				}
				break;
			case 'artifact':
				artifactCount += 1;
				break;
			case 'subagent':
				subagentCount += 1;
				if (part.status === 'error') {
					hasError = true;
				}
				break;
			case 'error':
				hasError = true;
				break;
			default:
				break;
		}
	}

	return {
		toolCount,
		artifactCount,
		approvalCount,
		userInputCount,
		subagentCount,
		hasError,
		hasPendingApproval,
		hasPendingUserInput
	};
};

const buildParts = (state: AgentRunEventState): AgentTranscriptModelPart[] => {
	const parts: AgentTranscriptModelPart[] = [];

	for (const block of state.textBlocks) {
		parts.push(textBlockToPart(block));
	}

	const tools = new Map<string, ToolAccumulator>();
	const approvals = new Map<string, ApprovalAccumulator>();
	const userInputs = new Map<string, UserInputAccumulator>();
	const artifacts = new Map<string, AgentRunEventViewItem>();
	const subagents = new Map<string, SubagentAccumulator>();

	for (const item of state.items) {
		switch (item.eventType) {
			case 'tool.requested':
			case 'tool.started':
			case 'tool.completed':
			case 'tool.failed': {
				const toolCallId = readPayloadString(item.details, 'tool_call_id', 'call_id');
				const key = toolCallId ?? `seq:${item.seq}`;
				const acc = tools.get(key) ?? {
					toolCallId: toolCallId ?? key,
					firstItem: item,
					lastItem: item
				};
				acc.lastItem = item;
				tools.set(key, acc);
				break;
			}
			case 'approval.requested':
			case 'approval.completed': {
				const approvalId = readPayloadString(item.details, 'approval_id');
				const key = approvalId ?? `seq:${item.seq}`;
				const acc = approvals.get(key) ?? {
					approvalId: approvalId ?? key,
					firstItem: item,
					lastItem: item
				};
				acc.lastItem = item;
				approvals.set(key, acc);
				break;
			}
			case 'user_input.requested':
			case 'user_input.completed':
			case 'user_input.declined':
			case 'user_input.cancelled':
			case 'user_input.expired': {
				const userInputId = readPayloadString(item.details, 'user_input_id');
				const key = userInputId ?? `seq:${item.seq}`;
				const acc = userInputs.get(key) ?? {
					userInputId: userInputId ?? key,
					firstItem: item,
					lastItem: item
				};
				acc.lastItem = item;
				userInputs.set(key, acc);
				break;
			}
			case 'artifact.registered': {
				const artifactDetails = extractArtifactPayload(item.details);
				const artifactId =
					readPayloadString(artifactDetails, 'artifact_id', 'id') ??
					readPayloadString(item.details, 'artifact_id', 'id');
				const key = artifactId ?? `seq:${item.seq}`;
				if (!artifacts.has(key)) {
					artifacts.set(key, item);
				} else {
					const existing = artifacts.get(key);
					if (existing && item.seq > existing.seq) {
						artifacts.set(key, item);
					}
				}
				break;
			}
			case 'subagent.created':
			case 'subagent.updated':
			case 'subagent.completed':
			case 'subagent.failed': {
				const participantId = item.participantId ?? `seq:${item.seq}`;
				const acc = subagents.get(participantId) ?? {
					participantId,
					firstItem: item,
					lastItem: item
				};
				acc.lastItem = item;
				subagents.set(participantId, acc);
				break;
			}
			default:
				break;
		}
	}

	for (const acc of tools.values()) {
		parts.push(toolAccumulatorToPart(acc));
	}
	for (const acc of approvals.values()) {
		parts.push(approvalAccumulatorToPart(acc, state.runStatus));
	}
	for (const acc of userInputs.values()) {
		parts.push(userInputAccumulatorToPart(acc, state.runStatus));
	}
	for (const item of artifacts.values()) {
		const part = artifactItemToPart(item);
		if (part) {
			parts.push(part);
		}
	}
	for (const acc of subagents.values()) {
		parts.push(subagentAccumulatorToPart(acc));
	}

	for (const item of state.items) {
		if (
			item.eventType === 'run.failed' ||
			item.eventType === 'run.cancelled' ||
			item.eventType === 'run.budget_exceeded'
		) {
			parts.push(runErrorItemToPart(item));
		}
	}

	parts.sort((a, b) => a.seq - b.seq);
	return parts;
};

type ToolAccumulator = {
	toolCallId: string;
	firstItem: AgentRunEventViewItem;
	lastItem: AgentRunEventViewItem;
};

type ApprovalAccumulator = {
	approvalId: string;
	firstItem: AgentRunEventViewItem;
	lastItem: AgentRunEventViewItem;
};

type UserInputAccumulator = {
	userInputId: string;
	firstItem: AgentRunEventViewItem;
	lastItem: AgentRunEventViewItem;
};

type SubagentAccumulator = {
	participantId: string;
	firstItem: AgentRunEventViewItem;
	lastItem: AgentRunEventViewItem;
};

const textBlockToPart = (
	block: AgentRunEventState['textBlocks'][number]
): AgentTranscriptTextPart => {
	const kind = textKindToPartKind(block.kind);
	return {
		kind,
		textKind: block.kind,
		blockId: block.id,
		text: block.text,
		status: block.status,
		seq: block.firstSeq,
		createdAt: block.createdAt,
		participantId: block.participantId,
		phase: block.phase,
		defaultExpanded: false
	};
};

const textKindToPartKind = (kind: AgentTextBlockKind): AgentTranscriptTextPart['kind'] => {
	if (kind === 'assistant_note') {
		return 'assistant_note';
	}
	if (kind === 'action_summary') {
		return 'action_summary';
	}
	return 'legacy_note';
};

const toolAccumulatorToPart = (acc: ToolAccumulator): AgentTranscriptToolPart => {
	const last = acc.lastItem;
	const details = pickRicherDetails(acc.firstItem, acc.lastItem);
	const toolName =
		readPayloadString(details, 'tool_name', 'name', 'tool') ??
		extractToolNameFromSummary(last.summary);
	const status = resolveToolStatus(last.eventType);
	const summary =
		last.summary ||
		(toolName ? `${status === 'error' ? `${toolName} failed` : `Completed ${toolName}`}` : 'Tool');
	return {
		kind: 'tool',
		toolCallId: acc.toolCallId,
		toolName,
		label: 'Tool',
		status,
		summary,
		metadata: last.metadata,
		details,
		seq: acc.firstItem.seq,
		createdAt: acc.firstItem.createdAt,
		participantId: acc.firstItem.participantId,
		phase: acc.firstItem.phase,
		defaultExpanded: status === 'error'
	};
};

const approvalAccumulatorToPart = (
	acc: ApprovalAccumulator,
	runStatus: AgentRunState
): AgentTranscriptApprovalPart => {
	const last = acc.lastItem;
	const details = mergeDetails(acc.firstItem, acc.lastItem);
	const action = readPayloadString(details, 'action', 'description');
	const status =
		last.eventType === 'approval.completed'
			? resolveApprovalStatus(details)
			: runStatus === 'waiting_approval'
				? 'pending'
				: 'stale';
	const summary = last.summary || (action ? `Approval: ${action}` : 'Approval requested');
	return {
		kind: 'approval',
		approvalId: acc.approvalId,
		action,
		description: summary,
		status,
		metadata: last.metadata,
		details,
		seq: acc.firstItem.seq,
		createdAt: acc.firstItem.createdAt,
		participantId: acc.firstItem.participantId,
		phase: acc.firstItem.phase,
		defaultExpanded: status === 'pending'
	};
};

const userInputAccumulatorToPart = (
	acc: UserInputAccumulator,
	runStatus: AgentRunState
): AgentTranscriptUserInputPart => {
	const last = acc.lastItem;
	const requestDetails = acc.firstItem.details;
	const details = mergeDetails(acc.firstItem, acc.lastItem);
	const status = resolveUserInputStatus(last, runStatus);
	const message =
		readPayloadString(requestDetails, 'message', 'prompt', 'question') ||
		readPayloadString(details, 'message', 'prompt', 'question') ||
		'Needs your input';
	const requestedSchema = readPayloadObject(requestDetails, 'requested_schema', 'requestedSchema');
	return {
		kind: 'user_input',
		userInputId: acc.userInputId,
		message,
		status,
		requestedSchema,
		content: details?.content ?? null,
		allowCancel: readPayloadBoolean(requestDetails, 'allow_cancel', 'allowCancel') ?? true,
		metadata: last.metadata,
		details,
		seq: acc.firstItem.seq,
		createdAt: acc.firstItem.createdAt,
		participantId: acc.firstItem.participantId,
		phase: acc.firstItem.phase,
		defaultExpanded: status === 'pending'
	};
};

const artifactItemToPart = (item: AgentRunEventViewItem): AgentTranscriptArtifactPart | null => {
	const details = extractArtifactPayload(item.details);
	if (!details) {
		return null;
	}
	const artifactId = readPayloadString(details, 'artifact_id', 'id') ?? `seq:${item.seq}`;
	const name = readPayloadString(details, 'name') ?? null;
	const path = readPayloadString(details, 'path') ?? null;
	const mimeType = readPayloadString(details, 'mime_type') ?? null;
	const summary = item.summary || (name ? `Registered ${name}` : 'Artifact registered');
	return {
		kind: 'artifact',
		artifactId,
		name,
		path,
		mimeType,
		summary,
		details,
		seq: item.seq,
		createdAt: item.createdAt,
		participantId: item.participantId,
		phase: item.phase,
		defaultExpanded: false
	};
};

const subagentAccumulatorToPart = (acc: SubagentAccumulator): AgentTranscriptSubagentPart => {
	const last = acc.lastItem;
	const details = pickRicherDetails(acc.firstItem, acc.lastItem);
	const participantName =
		readPayloadString(details, 'participant_name', 'name') ?? acc.participantId;
	const status =
		last.eventType === 'subagent.failed'
			? 'error'
			: last.eventType === 'subagent.completed'
				? 'done'
				: 'running';
	const resultSummary = readPayloadString(details, 'result_summary', 'content');
	const summary =
		last.summary ||
		(participantName
			? `${status === 'error' ? `${participantName} failed` : `Completed ${participantName}`}`
			: 'Subagent');
	return {
		kind: 'subagent',
		participantName,
		label: 'Subagent',
		status,
		summary,
		resultSummary,
		details,
		seq: acc.firstItem.seq,
		createdAt: acc.firstItem.createdAt,
		participantId: acc.firstItem.participantId,
		phase: acc.firstItem.phase,
		defaultExpanded: status === 'error'
	};
};

const runErrorItemToPart = (item: AgentRunEventViewItem): AgentTranscriptErrorPart => {
	return {
		kind: 'error',
		label: item.label || 'Error',
		summary: item.summary || 'Run failed',
		details: item.details,
		seq: item.seq,
		createdAt: item.createdAt,
		participantId: item.participantId,
		phase: item.phase,
		defaultExpanded: true
	};
};

const resolveToolStatus = (
	eventType: AgentRunEventViewItem['eventType']
): AgentTranscriptToolPart['status'] => {
	if (eventType === 'tool.failed') {
		return 'error';
	}
	if (eventType === 'tool.completed') {
		return 'done';
	}
	return 'running';
};

const resolveApprovalStatus = (
	details: AgentRunEventPayload | null
): AgentTranscriptApprovalPart['status'] => {
	const raw = readPayloadString(details, 'status', 'decision');
	if (raw === 'approved' || raw === 'rejected') {
		return raw;
	}
	return 'approved';
};

const resolveUserInputStatus = (
	item: AgentRunEventViewItem,
	runStatus: AgentRunState
): AgentTranscriptUserInputPart['status'] => {
	if (item.eventType === 'user_input.requested') {
		return runStatus === 'waiting_user_input' ? 'pending' : 'stale';
	}
	if (item.eventType === 'user_input.declined') {
		return 'declined';
	}
	if (item.eventType === 'user_input.cancelled') {
		return 'cancelled';
	}
	if (item.eventType === 'user_input.expired') {
		return 'timeout';
	}
	const raw = readPayloadString(item.details, 'status');
	if (raw === 'accepted' || raw === 'declined' || raw === 'cancelled' || raw === 'timeout') {
		return raw;
	}
	return 'accepted';
};

const mergeDetails = (
	a: AgentRunEventViewItem,
	b: AgentRunEventViewItem
): AgentRunEventPayload | null => {
	if (a.details || b.details) {
		return { ...(a.details ?? {}), ...(b.details ?? {}) };
	}
	return null;
};

const extractArtifactPayload = (
	payload: AgentRunEventPayload | null | undefined
): AgentRunEventPayload | null => {
	if (!payload) {
		return null;
	}

	if (isPlainPayload(payload.artifact)) {
		return payload.artifact;
	}

	const artifacts = payload.artifacts;
	if (Array.isArray(artifacts)) {
		const firstArtifact = artifacts.find(isPlainPayload);
		if (firstArtifact) {
			return firstArtifact;
		}
	}

	return payload;
};

const pickRicherDetails = (
	a: AgentRunEventViewItem,
	b: AgentRunEventViewItem
): AgentRunEventPayload | null => {
	if (a.details && b.details) {
		const aKeys = Object.keys(a.details).length;
		const bKeys = Object.keys(b.details).length;
		return aKeys >= bKeys ? a.details : b.details;
	}
	return a.details ?? b.details;
};

const readPayloadString = (
	payload: AgentRunEventPayload | null | undefined,
	...keys: string[]
): string | null => {
	if (!payload) {
		return null;
	}
	for (const key of keys) {
		const value = payload[key];
		if (typeof value === 'string' && value.length > 0) {
			return value;
		}
		if (typeof value === 'number' && Number.isFinite(value)) {
			return `${value}`;
		}
	}
	return null;
};

const readPayloadObject = (
	payload: AgentRunEventPayload | null | undefined,
	...keys: string[]
): AgentRunEventPayload | null => {
	if (!payload) {
		return null;
	}
	for (const key of keys) {
		const value = payload[key];
		if (isPlainPayload(value)) {
			return value;
		}
	}
	return null;
};

const readPayloadBoolean = (
	payload: AgentRunEventPayload | null | undefined,
	...keys: string[]
): boolean | null => {
	if (!payload) {
		return null;
	}
	for (const key of keys) {
		const value = payload[key];
		if (typeof value === 'boolean') {
			return value;
		}
	}
	return null;
};

const isPlainPayload = (value: unknown): value is AgentRunEventPayload => {
	return typeof value === 'object' && value !== null && !Array.isArray(value);
};

const extractToolNameFromSummary = (summary: string): string | null => {
	if (!summary) {
		return null;
	}
	const match = summary.match(/(?:Requested|Running|Completed)\s+(.+)$/);
	return match ? match[1] : null;
};

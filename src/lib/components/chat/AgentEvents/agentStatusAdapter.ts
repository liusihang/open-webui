import type { AgentRunEvent, AgentRunEventPayload } from './types';

export type AgentStatusKind =
	| 'tool'
	| 'approval'
	| 'artifact'
	| 'subagent'
	| 'thinking'
	| 'text'
	| 'step'
	| 'error';

export type AgentStatusDetail = {
	input?: unknown;
	output?: unknown;
	error?: { message?: string; code?: string } | string;
	artifact?: {
		id: string;
		name: string;
		mimeType?: string;
		path?: string;
		size?: string;
	};
	subagent?: {
		name?: string;
		resultSummary?: string;
	};
	text?: {
		blockId: string;
		content: string;
		participantId?: string | null;
	};
};

export type AgentStatusEntry = {
	id: string;
	done: boolean;
	action: string;
	description: string;
	kind: AgentStatusKind;
	detail?: AgentStatusDetail;
	urls?: string[];
	query?: string;
	seq: number;
	created_at: number;
};

const THINKING_ID = 'thinking:run';
const THINKING_DESCRIPTION = '思考中...';

export const foldAgentEventIntoStatusHistory = (
	history: AgentStatusEntry[],
	event: AgentRunEvent
): AgentStatusEntry[] => {
	const next = applyEvent(history, event);
	if (next === history) {
		return history;
	}
	return [...next];
};

const applyEvent = (history: AgentStatusEntry[], event: AgentRunEvent): AgentStatusEntry[] => {
	switch (event.event_type) {
		case 'run.queued':
		case 'run.running':
			return upsert(history, THINKING_ID, {
				done: false,
				action: 'agent_thinking',
				description: THINKING_DESCRIPTION,
				kind: 'thinking',
				seq: event.seq,
				created_at: event.created_at
			});

		case 'run.completed':
			return markThinkingDone(markTextSegmentsDone(history, event), event);

		case 'run.failed':
		case 'run.cancelled':
		case 'run.budget_exceeded':
			return appendRunError(markThinkingDone(markTextSegmentsDone(history, event), event), event);

		case 'text.delta':
			return upsertText(history, event);

		case 'tool.requested':
		case 'tool.started':
			return upsertTool(markTextSegmentsDone(history, event), event, false);

		case 'tool.completed':
			return upsertTool(history, event, true);

		case 'tool.failed':
			return upsertToolError(history, event);

		case 'approval.requested':
			return upsertApproval(markTextSegmentsDone(history, event), event);

		case 'approval.completed':
			return updateApprovalDone(history, event);

		case 'artifact.registered':
			return upsertArtifact(history, event);

		case 'subagent.created':
		case 'subagent.updated':
			return upsertSubagent(markTextSegmentsDone(history, event), event, false);

		case 'subagent.completed':
			return upsertSubagent(history, event, true);

		case 'subagent.failed':
			return upsertSubagentError(history, event);

		case 'action.summary':
			return appendStep(history, event);

		case 'model.selection.requested':
		case 'model.selection.completed':
		case 'final.started':
		case 'final.delta':
			return history;

		default:
			return history;
	}
};

const upsert = (
	history: AgentStatusEntry[],
	id: string,
	partial: Omit<AgentStatusEntry, 'id'>
): AgentStatusEntry[] => {
	const index = history.findIndex((entry) => entry.id === id);
	if (index === -1) {
		return [...history, { id, ...partial }];
	}
	const updated = { ...history[index], ...partial };
	if (shallowEqual(history[index], updated)) {
		return history;
	}
	return history.map((entry, idx) => (idx === index ? updated : entry));
};

const upsertText = (history: AgentStatusEntry[], event: AgentRunEvent): AgentStatusEntry[] => {
	const blockId = firstString(event.payload.block_id, event.payload.blockId);
	if (!blockId) {
		return history;
	}

	const id = `text:${blockId}`;
	const delta = firstString(event.payload.delta, event.payload.text) ?? '';
	const existing = history.find((entry) => entry.id === id);
	const previousContent = existing?.detail?.text?.content ?? '';

	return upsert(history, id, {
		done: false,
		action: 'agent_text',
		description: '',
		kind: 'text',
		seq: event.seq,
		created_at: event.created_at,
		detail: {
			text: {
				blockId,
				content: previousContent + delta,
				participantId: event.participant_id ?? null
			}
		}
	});
};

const markThinkingDone = (
	history: AgentStatusEntry[],
	event: AgentRunEvent
): AgentStatusEntry[] => {
	const index = history.findIndex((entry) => entry.id === THINKING_ID);
	if (index === -1) {
		return history;
	}
	const existing = history[index];
	if (existing.done && existing.seq >= event.seq) {
		return history;
	}
	const updated = { ...existing, done: true, seq: event.seq, created_at: event.created_at };
	return history.map((entry, idx) => (idx === index ? updated : entry));
};

const markTextSegmentsDone = (
	history: AgentStatusEntry[],
	event: AgentRunEvent
): AgentStatusEntry[] => {
	let changed = false;
	const next = history.map((entry) => {
		if (entry.kind !== 'text' || entry.done) {
			return entry;
		}

		changed = true;
		return { ...entry, done: true, seq: event.seq, created_at: event.created_at };
	});

	return changed ? next : history;
};

const appendRunError = (history: AgentStatusEntry[], event: AgentRunEvent): AgentStatusEntry[] => {
	const description = runErrorDescription(event);
	const error = extractError(event.payload);
	const entry: AgentStatusEntry = {
		id: `error:${event.seq}`,
		done: true,
		action: 'agent_error',
		description,
		kind: 'error',
		seq: event.seq,
		created_at: event.created_at,
		detail: error === null ? undefined : { error }
	};
	return [...history, entry];
};

const runErrorDescription = (event: AgentRunEvent): string => {
	switch (event.event_type) {
		case 'run.failed':
			return firstString(event.summary, errorMessage(event.payload)) ?? '任务失败';
		case 'run.cancelled':
			return firstString(event.summary) ?? '任务已取消';
		case 'run.budget_exceeded':
			return firstString(event.summary) ?? '任务达到限制';
		default:
			return firstString(event.summary) ?? '任务出错';
	}
};

const upsertTool = (
	history: AgentStatusEntry[],
	event: AgentRunEvent,
	done: boolean
): AgentStatusEntry[] => {
	const payload = event.payload;
	const toolCallId = firstString(payload.tool_call_id, payload.call_id);
	const id = toolCallId ? `tool:${toolCallId}` : `tool:seq-${event.seq}`;
	const name = humanizeIdentifier(firstString(payload.tool_name, payload.name, payload.tool));
	const description = name ?? event.summary ?? '工具调用';
	const existing = history.find((entry) => entry.id === id);
	const detail: AgentStatusDetail | undefined = done
		? { ...(existing?.detail ?? {}), output: payload.result ?? payload.content ?? null }
		: { input: payload.arguments ?? payload.query ?? null };

	return upsert(history, id, {
		done,
		action: 'tool',
		description,
		kind: 'tool',
		seq: event.seq,
		created_at: event.created_at,
		detail: stripEmptyDetail(detail)
	});
};

const upsertToolError = (history: AgentStatusEntry[], event: AgentRunEvent): AgentStatusEntry[] => {
	const payload = event.payload;
	const toolCallId = firstString(payload.tool_call_id, payload.call_id);
	const id = toolCallId ? `tool:${toolCallId}` : `tool:seq-${event.seq}`;
	const error = extractError(payload);
	const existing = history.find((entry) => entry.id === id);
	const description =
		existing?.description ??
		humanizeIdentifier(firstString(payload.tool_name, payload.name, payload.tool)) ??
		event.summary ??
		'工具调用失败';

	return upsert(history, id, {
		done: true,
		action: 'tool',
		description,
		kind: 'error',
		seq: event.seq,
		created_at: event.created_at,
		detail: stripEmptyDetail({
			...(existing?.detail ?? {}),
			error: error ?? { message: firstString(payload.error, payload.message) ?? '工具调用失败' }
		})
	});
};

const upsertApproval = (history: AgentStatusEntry[], event: AgentRunEvent): AgentStatusEntry[] => {
	const payload = event.payload;
	const approvalId = firstString(payload.approval_id, payload.id);
	const id = approvalId ? `approval:${approvalId}` : `approval:seq-${event.seq}`;
	const description =
		firstString(payload.description, payload.action, payload.request) ??
		event.summary ??
		'需要确认';
	const detail: AgentStatusDetail = {
		input: pickFields(payload, ['action', 'description', 'request'])
	};

	return upsert(history, id, {
		done: false,
		action: 'approval',
		description,
		kind: 'approval',
		seq: event.seq,
		created_at: event.created_at,
		detail: stripEmptyDetail(detail)
	});
};

const updateApprovalDone = (
	history: AgentStatusEntry[],
	event: AgentRunEvent
): AgentStatusEntry[] => {
	const payload = event.payload;
	const approvalId = firstString(payload.approval_id, payload.id);
	const id = approvalId ? `approval:${approvalId}` : `approval:seq-${event.seq}`;
	const index = history.findIndex((entry) => entry.id === id);
	if (index === -1) {
		return history;
	}
	const updated = { ...history[index], done: true, seq: event.seq, created_at: event.created_at };
	return history.map((entry, idx) => (idx === index ? updated : entry));
};

const upsertArtifact = (history: AgentStatusEntry[], event: AgentRunEvent): AgentStatusEntry[] => {
	const payload = event.payload;
	const artifactId = firstString(payload.artifact_id, payload.id, payload.path, payload.name);
	const id = artifactId ? `artifact:${artifactId}` : `artifact:seq-${event.seq}`;
	const name = firstString(payload.name, payload.path, payload.artifact_id) ?? '文件';
	const artifact = {
		id: artifactId ?? id,
		name,
		mimeType: firstString(payload.mime_type) ?? undefined,
		path: firstString(payload.path) ?? undefined,
		size: firstString(payload.size) ?? undefined
	};

	return upsert(history, id, {
		done: true,
		action: 'artifact',
		description: name,
		kind: 'artifact',
		seq: event.seq,
		created_at: event.created_at,
		detail: { artifact }
	});
};

const upsertSubagent = (
	history: AgentStatusEntry[],
	event: AgentRunEvent,
	done: boolean
): AgentStatusEntry[] => {
	const payload = event.payload;
	const participantId = event.participant_id ?? firstString(payload.participant_id);
	const id = participantId ? `subagent:${participantId}` : `subagent:seq-${event.seq}`;
	const name = firstString(payload.participant_name, payload.name) ?? event.summary ?? '助手';
	const existing = history.find((entry) => entry.id === id);
	const detail: AgentStatusDetail | undefined = done
		? {
				...(existing?.detail ?? {}),
				subagent: {
					name,
					resultSummary: firstString(payload.result_summary, payload.status) ?? undefined
				}
			}
		: { subagent: { name } };

	return upsert(history, id, {
		done,
		action: 'subagent',
		description: name,
		kind: 'subagent',
		seq: event.seq,
		created_at: event.created_at,
		detail: stripEmptyDetail(detail)
	});
};

const upsertSubagentError = (
	history: AgentStatusEntry[],
	event: AgentRunEvent
): AgentStatusEntry[] => {
	const payload = event.payload;
	const participantId = event.participant_id ?? firstString(payload.participant_id);
	const id = participantId ? `subagent:${participantId}` : `subagent:seq-${event.seq}`;
	const existing = history.find((entry) => entry.id === id);
	const name =
		existing?.description ?? firstString(payload.participant_name, payload.name) ?? '助手';
	const error = extractError(payload);

	return upsert(history, id, {
		done: true,
		action: 'subagent',
		description: name,
		kind: 'error',
		seq: event.seq,
		created_at: event.created_at,
		detail: stripEmptyDetail({
			...(existing?.detail ?? {}),
			error: error ?? { message: firstString(payload.error, payload.message) ?? '子助手失败' }
		})
	});
};

const appendStep = (history: AgentStatusEntry[], event: AgentRunEvent): AgentStatusEntry[] => {
	const description = event.summary?.trim() || '执行步骤';
	const entry: AgentStatusEntry = {
		id: `step:${event.seq}`,
		done: true,
		action: 'action_summary',
		description,
		kind: 'step',
		seq: event.seq,
		created_at: event.created_at
	};
	return [...history, entry];
};

const extractError = (
	payload: AgentRunEventPayload
): { message?: string; code?: string } | string | null => {
	const err = payload.structured_error ?? payload.error;
	if (typeof err === 'string') {
		return err;
	}
	if (isPlainObject(err)) {
		const message = firstString(err.message, err.error);
		const code = firstString(err.code);
		if (message || code) {
			const result: { message?: string; code?: string } = {};
			if (message) result.message = message;
			if (code) result.code = code;
			return result;
		}
	}
	const message = firstString(payload.message);
	return message === null ? null : { message };
};

const errorMessage = (payload: AgentRunEventPayload): string | null => {
	const err = extractError(payload);
	if (err === null) return null;
	if (typeof err === 'string') return err;
	return err.message ?? null;
};

const pickFields = (value: AgentRunEventPayload, keys: string[]): AgentRunEventPayload => {
	const picked: AgentRunEventPayload = {};
	for (const key of keys) {
		if (value[key] !== undefined && value[key] !== null) {
			picked[key] = value[key];
		}
	}
	return picked;
};

const stripEmptyDetail = (detail: AgentStatusDetail): AgentStatusDetail | undefined => {
	if (
		detail.input === undefined &&
		detail.output === undefined &&
		detail.error === undefined &&
		detail.artifact === undefined &&
		detail.subagent === undefined &&
		detail.text === undefined
	) {
		return undefined;
	}
	const cleaned: AgentStatusDetail = {};
	if (detail.input !== undefined) cleaned.input = detail.input;
	if (detail.output !== undefined) cleaned.output = detail.output;
	if (detail.error !== undefined) cleaned.error = detail.error;
	if (detail.artifact !== undefined) cleaned.artifact = detail.artifact;
	if (detail.subagent !== undefined) cleaned.subagent = detail.subagent;
	if (detail.text !== undefined) cleaned.text = detail.text;
	return cleaned;
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

const humanizeIdentifier = (value: string | null): string | null => {
	if (!value) return null;
	return value
		.replace(/[_-]+/g, ' ')
		.replace(/\s+/g, ' ')
		.trim()
		.replace(/\b\w/g, (letter) => letter.toUpperCase());
};

const isPlainObject = (value: unknown): value is Record<string, unknown> => {
	return value !== null && typeof value === 'object' && !Array.isArray(value);
};

const shallowEqual = (a: AgentStatusEntry, b: AgentStatusEntry): boolean => {
	return (
		a.id === b.id &&
		a.done === b.done &&
		a.action === b.action &&
		a.description === b.description &&
		a.kind === b.kind &&
		a.seq === b.seq &&
		a.created_at === b.created_at &&
		a.detail === b.detail
	);
};

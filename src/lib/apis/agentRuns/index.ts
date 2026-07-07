import { WEBUI_BASE_URL } from '$lib/constants';
import type { AgentRun, AgentRunEvent } from '$lib/components/chat/AgentEvents/types';

export type AgentRunListOptions = {
	chatId?: string;
	state?: string;
	limit?: number;
	page?: number;
};

export type AgentRunEventsOptions = {
	afterSeq?: number;
	lastEventId?: string;
};

export type AgentRunUserInputSubmission = {
	status: 'accepted' | 'declined' | 'cancelled';
	content?: unknown;
	idempotencyKey?: string;
};

export type AgentRunListResponse = {
	items: AgentRun[];
	total: number;
};

const jsonHeaders = (token: string = '') => ({
	Accept: 'application/json',
	'Content-Type': 'application/json',
	...(token && { authorization: `Bearer ${token}` })
});

const AGENT_RUNS_API_BASE_URL = `${WEBUI_BASE_URL}/api/agent/runs`;

export const getAgentRuns = async (
	token: string = '',
	options: AgentRunListOptions = {}
): Promise<AgentRunListResponse> => {
	let error = null;
	const searchParams = new URLSearchParams();

	if (options.chatId) {
		searchParams.append('chat_id', options.chatId);
	}
	if (options.state) {
		searchParams.append('state', options.state);
	}
	if (options.limit !== undefined) {
		searchParams.append('limit', `${options.limit}`);
	}
	if (options.page !== undefined) {
		searchParams.append('page', `${options.page}`);
	}

	const res = await fetch(`${AGENT_RUNS_API_BASE_URL}?${searchParams.toString()}`, {
		method: 'GET',
		credentials: 'include',
		headers: jsonHeaders(token)
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err?.detail ?? err;
			console.error(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res ?? { items: [], total: 0 };
};

export const getAgentRunEvents = async (
	token: string = '',
	runId: string,
	options: AgentRunEventsOptions = {}
): Promise<AgentRunEvent[]> => {
	let error = null;
	const res = await fetch(buildAgentRunEventsListUrl(runId, options), {
		method: 'GET',
		credentials: 'include',
		headers: jsonHeaders(token)
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err?.detail ?? err;
			console.error(err);
			return null;
		});

	if (error) {
		throw error;
	}

	if (Array.isArray(res)) {
		return res;
	}

	return res?.events ?? [];
};

const buildAgentRunEventsSearchParams = (options: AgentRunEventsOptions = {}): URLSearchParams => {
	const searchParams = new URLSearchParams();

	if (options.afterSeq !== undefined) {
		searchParams.append('after_seq', `${options.afterSeq}`);
	}
	if (options.lastEventId) {
		searchParams.append('last_event_id', options.lastEventId);
	}

	return searchParams;
};

export const buildAgentRunEventsListUrl = (
	runId: string,
	options: AgentRunEventsOptions = {}
): string => {
	const searchParams = buildAgentRunEventsSearchParams(options);

	const query = searchParams.toString();
	return `${AGENT_RUNS_API_BASE_URL}/${encodeURIComponent(runId)}/events/list${
		query ? `?${query}` : ''
	}`;
};

export const buildAgentRunEventsUrl = (
	runId: string,
	options: AgentRunEventsOptions = {}
): string => {
	const searchParams = buildAgentRunEventsSearchParams(options);

	const query = searchParams.toString();
	return `${AGENT_RUNS_API_BASE_URL}/${encodeURIComponent(runId)}/events${
		query ? `?${query}` : ''
	}`;
};

export const createAgentRunEventsSource = (
	runId: string,
	options: AgentRunEventsOptions = {}
): EventSource => {
	return new EventSource(buildAgentRunEventsUrl(runId, options), {
		withCredentials: true
	});
};

export const submitAgentRunUserInput = async (
	token: string = '',
	runId: string,
	userInputId: string,
	submission: AgentRunUserInputSubmission
): Promise<Record<string, unknown>> => {
	let error = null;
	const idempotencyKey = submission.idempotencyKey ?? createIdempotencyKey(userInputId, submission.status);
	const res = await fetch(
		`${AGENT_RUNS_API_BASE_URL}/${encodeURIComponent(runId)}/user-input/${encodeURIComponent(
			userInputId
		)}`,
		{
			method: 'POST',
			credentials: 'include',
			headers: {
				...jsonHeaders(token),
				'X-Agent-Idempotency-Key': idempotencyKey
			},
			body: JSON.stringify({
				run_id: runId,
				user_input_id: userInputId,
				status: submission.status,
				content: submission.content,
				idempotency_key: idempotencyKey
			})
		}
	)
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err?.detail ?? err;
			console.error(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res ?? {};
};

const createIdempotencyKey = (userInputId: string, status: string): string => {
	const random =
		typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
			? crypto.randomUUID()
			: `${Date.now()}-${Math.random().toString(36).slice(2)}`;
	return `user-input:${userInputId}:${status}:${random}`;
};

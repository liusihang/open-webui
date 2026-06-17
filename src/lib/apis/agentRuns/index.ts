import { WEBUI_API_BASE_URL } from '$lib/constants';
import type {
	AgentRun,
	AgentRunEvent
} from '$lib/components/chat/AgentEvents/types';

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

export type AgentRunListResponse = {
	items: AgentRun[];
	total: number;
};

const jsonHeaders = (token: string = '') => ({
	Accept: 'application/json',
	'Content-Type': 'application/json',
	...(token && { authorization: `Bearer ${token}` })
});

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

	const res = await fetch(`${WEBUI_API_BASE_URL}/agent/runs?${searchParams.toString()}`, {
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
	const res = await fetch(buildAgentRunEventsUrl(runId, options), {
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

export const buildAgentRunEventsUrl = (
	runId: string,
	options: AgentRunEventsOptions = {}
): string => {
	const searchParams = new URLSearchParams();

	if (options.afterSeq !== undefined) {
		searchParams.append('after_seq', `${options.afterSeq}`);
	}
	if (options.lastEventId) {
		searchParams.append('last_event_id', options.lastEventId);
	}

	const query = searchParams.toString();
	return `${WEBUI_API_BASE_URL}/agent/runs/${encodeURIComponent(runId)}/events${
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

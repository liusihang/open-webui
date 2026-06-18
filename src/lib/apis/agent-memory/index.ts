import { WEBUI_API_BASE_URL } from '$lib/constants';

const requestAgentMemory = async (
	token: string,
	path: string,
	method: 'GET' | 'POST' = 'POST',
	body: Record<string, unknown> | null = null
) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}${path}`, {
		method,
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		...(body !== null ? { body: JSON.stringify(body) } : {})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err.detail ?? err.message ?? `${err}`;
			console.error(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getAgentMemoryFailedJobs = async (token: string, userId = '') => {
	const params = new URLSearchParams();
	if (userId.trim()) {
		params.set('user_id', userId.trim());
	}
	const suffix = params.toString() ? `?${params.toString()}` : '';
	return await requestAgentMemory(token, `/agent-memory/jobs/failed${suffix}`, 'GET');
};

export const retryFailedAgentMemoryJobs = async (token: string, userId = '') => {
	return await requestAgentMemory(token, '/agent-memory/jobs/failed/retry', 'POST', {
		user_id: userId.trim() || null
	});
};

export const runAgentMemoryExtraction = async (token: string, limit: number | null = null) => {
	return await requestAgentMemory(token, '/agent-memory/extract/run', 'POST', { limit });
};

export const runAgentMemoryConsolidation = async (token: string, limit: number | null = null) => {
	return await requestAgentMemory(token, '/agent-memory/consolidate/run', 'POST', { limit });
};

export const rebuildAgentMemoryIndex = async (
	token: string,
	userId: string,
	scopeType: 'global' | 'folder' | null = null,
	scopeId = ''
) => {
	return await requestAgentMemory(token, '/agent-memory/index/rebuild', 'POST', {
		user_id: userId.trim(),
		scope_type: scopeType,
		scope_id: scopeType === 'folder' ? scopeId.trim() : null
	});
};

export const clearAgentMemory = async (
	token: string,
	userId: string,
	noteMode: 'convert' | 'delete' = 'convert',
	scopeType: 'global' | 'folder' | null = null,
	scopeId = ''
) => {
	return await requestAgentMemory(token, '/agent-memory/clear', 'POST', {
		user_id: userId.trim(),
		note_mode: noteMode,
		scope_type: scopeType,
		scope_id: scopeType === 'folder' ? scopeId.trim() : null
	});
};

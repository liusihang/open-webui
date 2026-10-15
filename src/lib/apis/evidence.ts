import { WEBUI_API_BASE_URL } from '$lib/constants';

async function response(path: string, token: string, init: RequestInit = {}) {
	const result = await fetch(`${WEBUI_API_BASE_URL}${path}`, {
		...init,
		cache: 'no-store',
		headers: { Authorization: `Bearer ${token}`, ...init.headers }
	});
	if (!result.ok) throw new Error('Source unavailable, updated, or no longer authorized');
	return result;
}

export const evidencePath = (fileId: string, ref: string) =>
	`/files/${encodeURIComponent(fileId)}/evidence/${encodeURIComponent(ref)}`;

export const getEvidence = async (
	token: string,
	fileId: string,
	ref: string,
	signal?: AbortSignal
) => (await response(evidencePath(fileId, ref), token, { signal })).json();

export const getEvidenceImage = async (
	token: string,
	fileId: string,
	ref: string,
	signal?: AbortSignal
) => (await response(`${evidencePath(fileId, ref)}/thumbnail`, token, { signal })).blob();

export const getRetrievalStatus = async (token: string, fileId: string, signal?: AbortSignal) =>
	(
		await response(`/files/${encodeURIComponent(fileId)}/retrieval/status`, token, { signal })
	).json();

export const retryImageIndex = async (token: string, fileId: string) =>
	(
		await response(`/files/${encodeURIComponent(fileId)}/retrieval/retry-images`, token, {
			method: 'POST'
		})
	).json();

export const getEvidenceSettings = async (token: string) =>
	(await response('/retrieval/evidence/config', token)).json();

export const saveEvidenceSettings = async (token: string, settings: Record<string, unknown>) =>
	(
		await response('/retrieval/evidence/config', token, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(settings)
		})
	).json();

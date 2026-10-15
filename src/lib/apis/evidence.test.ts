import { afterEach, describe, expect, it, vi } from 'vitest';
import {
	evidencePath,
	getEvidence,
	getEvidenceImage,
	retryImageIndex,
	saveEvidenceSettings
} from './evidence';

afterEach(() => vi.unstubAllGlobals());

describe('authorized evidence API', () => {
	it('encodes stored identities, not arbitrary route paths', () => {
		expect(evidencePath('file/id', 'ref?path=/secret')).toBe(
			'/files/file%2Fid/evidence/ref%3Fpath%3D%2Fsecret'
		);
	});

	it('uses bearer authorization, no-store and abortable JSON reads', async () => {
		const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: 'ready' })));
		vi.stubGlobal('fetch', fetcher);
		const controller = new AbortController();
		expect(await getEvidence('fixture-token', 'file', 'ke_ref', controller.signal)).toEqual({
			status: 'ready'
		});
		expect(fetcher.mock.calls[0][1]).toMatchObject({
			cache: 'no-store',
			signal: controller.signal,
			headers: { Authorization: 'Bearer fixture-token' }
		});
	});

	it('fetches image bytes through the same authorized endpoint', async () => {
		const fetcher = vi.fn().mockResolvedValue(
			new Response(new Uint8Array([1, 2, 3]), {
				headers: { 'Content-Type': 'image/jpeg' }
			})
		);
		vi.stubGlobal('fetch', fetcher);
		expect((await getEvidenceImage('fixture-token', 'file', 'ke_ref')).type).toBe('image/jpeg');
		expect(fetcher.mock.calls[0][0]).toContain('/files/file/evidence/ke_ref/thumbnail');
	});

	it('does not reuse cached content after revocation or source update', async () => {
		vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 404 })));
		await expect(getEvidence('fixture-token', 'file', 'old-ref')).rejects.toThrow(
			'Source unavailable'
		);
	});

	it('makes image retry an explicit POST action', async () => {
		const fetcher = vi.fn().mockResolvedValue(new Response('{"scheduled":1}'));
		vi.stubGlobal('fetch', fetcher);
		await retryImageIndex('fixture-token', 'file');
		expect(fetcher.mock.calls[0][1].method).toBe('POST');
	});

	it('saves settings without invoking a rebuild endpoint', async () => {
		const fetcher = vi.fn().mockResolvedValue(new Response('{}'));
		vi.stubGlobal('fetch', fetcher);
		await saveEvidenceSettings('fixture-token', {
			'rag.evidence_vector_space_id': 'space_verified'
		});
		expect(fetcher.mock.calls[0][0]).toContain('/retrieval/evidence/config');
		expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({
			'rag.evidence_vector_space_id': 'space_verified'
		});
	});
});

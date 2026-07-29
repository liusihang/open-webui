import { beforeEach, describe, expect, it, vi } from 'vitest';

import { generateOpenAIChatCompletion } from './index';

describe('generateOpenAIChatCompletion error responses', () => {
	beforeEach(() => {
		vi.restoreAllMocks();
	});

	it('returns a plain-text backend error instead of a JSON parser error', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue(
				new Response('Internal Server Error', {
					status: 500,
					statusText: 'Internal Server Error',
					headers: { 'content-type': 'text/plain; charset=utf-8' }
				})
			)
		);

		await expect(generateOpenAIChatCompletion('token-1', { model: 'openai/gpt-5.5' })).rejects.toBe(
			'Internal Server Error'
		);
	});

	it('preserves detail from a JSON backend error', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue(
				new Response(JSON.stringify({ detail: 'Model not found' }), {
					status: 404,
					headers: { 'content-type': 'application/json' }
				})
			)
		);

		await expect(generateOpenAIChatCompletion('token-1', { model: 'missing-model' })).rejects.toBe(
			'Model not found'
		);
	});
});

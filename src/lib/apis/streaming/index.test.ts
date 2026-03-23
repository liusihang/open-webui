import { describe, expect, it } from 'vitest';

import { collectSSEEventData } from './index';

const createStream = (chunks: string[]) =>
	new ReadableStream<Uint8Array>({
		start(controller) {
			for (const chunk of chunks) {
				controller.enqueue(new TextEncoder().encode(chunk));
			}
			controller.close();
		}
	});

describe('collectSSEEventData', () => {
	it('reassembles SSE events split across transport chunks', async () => {
		const events = await collectSSEEventData(
			createStream([
				'data: {"choices":[{"delta":{"content":"he',
				'llo"}}]}\n\n',
				'data: [DONE]\n\n'
			])
		);

		expect(events).toEqual(['{"choices":[{"delta":{"content":"hello"}}]}', '[DONE]']);
	});
});

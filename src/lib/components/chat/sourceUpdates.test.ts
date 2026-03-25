import { describe, expect, it } from 'vitest';

import { enqueueSourceBatch, enqueueSourceUpdate, flushQueuedSourceUpdates } from './sourceUpdates';


describe('source update batching helpers', () => {
	it('batches many source events into one merged message update', () => {
		const queue = new Map<string, unknown[]>();

		enqueueSourceUpdate(queue, 'm1', { source: { id: 'a' } });
		enqueueSourceUpdate(queue, 'm1', { source: { id: 'b' } });
		enqueueSourceUpdate(queue, 'm2', { source: { id: 'c' } });

		const nextMessages = flushQueuedSourceUpdates(
			{
				m1: { id: 'm1', sources: [{ source: { id: 'seed' } }] },
				m2: { id: 'm2' }
			},
			queue
		);

		expect(queue.size).toBe(0);
		expect(nextMessages.m1.sources).toEqual([
			{ source: { id: 'seed' } },
			{ source: { id: 'a' } },
			{ source: { id: 'b' } }
		]);
		expect(nextMessages.m2.sources).toEqual([{ source: { id: 'c' } }]);
	});

	it('ignores queued sources for missing messages', () => {
		const queue = new Map<string, unknown[]>();
		enqueueSourceUpdate(queue, 'missing', { source: { id: 'x' } });

		const nextMessages = flushQueuedSourceUpdates({}, queue);

		expect(nextMessages).toEqual({});
		expect(queue.size).toBe(0);
	});

	it('enqueues a batch of sources in arrival order', () => {
		const queue = new Map<string, unknown[]>();

		enqueueSourceBatch(queue, 'm1', [{ source: { id: 'a' } }, { source: { id: 'b' } }]);

		expect(queue.get('m1')).toEqual([{ source: { id: 'a' } }, { source: { id: 'b' } }]);
	});
});

import { describe, expect, it } from 'vitest';

import {
	LAYER_TYPE_ORDER,
	buildLayerViewModel,
	getLayerFallbackCopy,
	getLayerTitle,
	normalizeLayerStatus
} from './LayersPanel.svelte';

describe('LayersPanel helpers', () => {
	it('keeps fixed layer order and returns friendly titles', () => {
		expect(LAYER_TYPE_ORDER).toEqual(['abstract']);
		expect(getLayerTitle('abstract')).toBe('Abstract');
	});

	it('builds stable layer cards and marks missing layers as pending', () => {
		const viewModel = buildLayerViewModel([
			{
				layer_type: 'abstract',
				content: 'Main abstract text',
				status: 'ready',
				updated_at: 1710000000
			}
		]);

		expect(viewModel).toEqual([
			{
				layerType: 'abstract',
				title: 'Abstract',
				content: 'Main abstract text',
				status: 'ready',
				updatedAt: 1710000000
			}
		]);
	});

	it('returns clear fallback copy by status', () => {
		expect(getLayerFallbackCopy('pending')).toBe('Layer content is not available yet.');
		expect(getLayerFallbackCopy('stale')).toBe('Layer content is stale. Regenerate to refresh.');
		expect(getLayerFallbackCopy('failed')).toBe(
			'Layer generation failed. Try regenerating this layer.'
		);
	});

	it('normalizes unknown statuses to pending', () => {
		expect(normalizeLayerStatus('queued')).toBe('pending');
		expect(normalizeLayerStatus(null)).toBe('pending');
		expect(normalizeLayerStatus('ready')).toBe('ready');
	});

	it('combines chunked layer rows in part order', () => {
		const viewModel = buildLayerViewModel([
			{
				layer_type: 'abstract',
				content: 'second chunk',
				status: 'ready',
				part_index: 2,
				part_total: 2,
				display_title: 'Abstract 2/2',
				updated_at: 11
			},
			{
				layer_type: 'abstract',
				content: 'first chunk',
				status: 'ready',
				part_index: 1,
				part_total: 2,
				display_title: 'Abstract 1/2',
				updated_at: 10
			}
		]);

		expect(viewModel[0].content).toBe('Abstract 1/2: first chunk\n\nAbstract 2/2: second chunk');
		expect(viewModel[0].status).toBe('ready');
		expect(viewModel[0].updatedAt).toBe(11);
	});
});

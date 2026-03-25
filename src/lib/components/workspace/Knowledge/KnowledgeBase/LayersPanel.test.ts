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
		expect(LAYER_TYPE_ORDER).toEqual(['abstract', 'key_findings', 'key_data']);
		expect(getLayerTitle('abstract')).toBe('Abstract');
		expect(getLayerTitle('key_findings')).toBe('Key Findings');
		expect(getLayerTitle('key_data')).toBe('Key Data');
	});

	it('builds stable layer cards and marks missing layers as pending', () => {
		const viewModel = buildLayerViewModel([
			{
				layer_type: 'key_findings',
				content: 'Main findings text',
				status: 'ready',
				updated_at: 1710000000
			}
		]);

		expect(viewModel).toEqual([
			{
				layerType: 'abstract',
				title: 'Abstract',
				content: '',
				status: 'pending',
				updatedAt: null
			},
			{
				layerType: 'key_findings',
				title: 'Key Findings',
				content: 'Main findings text',
				status: 'ready',
				updatedAt: 1710000000
			},
			{
				layerType: 'key_data',
				title: 'Key Data',
				content: '',
				status: 'pending',
				updatedAt: null
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
});

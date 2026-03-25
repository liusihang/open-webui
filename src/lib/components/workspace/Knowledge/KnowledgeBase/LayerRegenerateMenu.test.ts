import { describe, expect, it } from 'vitest';

import {
	DEFAULT_SELECTED_LAYER_TYPES,
	toggleLayerType
} from './LayerRegenerateMenu.svelte';

describe('LayerRegenerateMenu helpers', () => {
	it('defaults to selecting all layer types', () => {
		expect(DEFAULT_SELECTED_LAYER_TYPES).toEqual(['abstract', 'key_findings', 'key_data']);
	});

	it('toggles layer selection while preserving at least one selected layer', () => {
		expect(toggleLayerType(['abstract', 'key_data'], 'abstract')).toEqual(['key_data']);
		expect(toggleLayerType(['abstract'], 'abstract')).toEqual(['abstract']);
		expect(toggleLayerType(['abstract'], 'key_data')).toEqual(['abstract', 'key_data']);
	});
});

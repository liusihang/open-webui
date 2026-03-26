import { describe, expect, it } from 'vitest';

import {
	DEFAULT_SELECTED_LAYER_TYPES,
	toggleLayerType
} from './LayerRegenerateMenu.svelte';

describe('LayerRegenerateMenu helpers', () => {
	it('defaults to selecting only abstract', () => {
		expect(DEFAULT_SELECTED_LAYER_TYPES).toEqual(['abstract']);
	});

	it('toggles layer selection while preserving at least one selected layer', () => {
		expect(toggleLayerType(['abstract'], 'abstract')).toEqual(['abstract']);
	});
});

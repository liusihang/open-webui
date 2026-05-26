import { describe, expect, it } from 'vitest';

import {
	resolveImageGenerationDraftState,
	resolveImageGenerationFeature,
	shouldEnableImageGenerationByDefault
} from './defaultFeatures';

const imageModel = (meta = {}) =>
	({
		info: {
			meta: {
				capabilities: { image_generation: true },
				builtinTools: { image_generation: true },
				...meta
			}
		}
	}) as any;

describe('shouldEnableImageGenerationByDefault', () => {
	it('uses explicit defaultFeatureIds when present', () => {
		expect(
			shouldEnableImageGenerationByDefault(imageModel({ defaultFeatureIds: [] }), true, true)
		).toBe(false);

		expect(
			shouldEnableImageGenerationByDefault(
				imageModel({ defaultFeatureIds: ['image_generation'] }),
				true,
				true
			)
		).toBe(true);
	});

	it('defaults image generation on when the model explicitly enables the builtin image tool', () => {
		expect(shouldEnableImageGenerationByDefault(imageModel(), true, true)).toBe(true);
	});

	it('does not default on without global feature access or model capability', () => {
		expect(shouldEnableImageGenerationByDefault(imageModel(), false, true)).toBe(false);
		expect(shouldEnableImageGenerationByDefault(imageModel(), true, false)).toBe(false);
		expect(
			shouldEnableImageGenerationByDefault(
				imageModel({ capabilities: { image_generation: false } }),
				true,
				true
			)
		).toBe(false);
	});
});

describe('resolveImageGenerationFeature', () => {
	it('restores old drafts without treating a saved false as a manual opt-out', () => {
		expect(
			resolveImageGenerationDraftState(
				{ imageGenerationEnabled: false },
				imageModel({ defaultFeatureIds: ['image_generation'] }),
				true,
				true
			)
		).toEqual({ enabled: true, userOverride: null });
	});

	it('preserves an explicit per-message image generation opt-out', () => {
		expect(
			resolveImageGenerationFeature(
				imageModel({ defaultFeatureIds: ['image_generation'] }),
				true,
				true,
				true,
				false
			)
		).toBe(false);
	});
});

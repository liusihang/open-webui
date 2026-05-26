import { describe, expect, it } from 'vitest';

import { shouldEnableImageGenerationByDefault } from './defaultFeatures';

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
			shouldEnableImageGenerationByDefault(
				imageModel({ defaultFeatureIds: [] }),
				true,
				true
			)
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

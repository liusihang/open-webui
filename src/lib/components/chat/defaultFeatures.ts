export const shouldEnableImageGenerationByDefault = (
	model: any,
	globalImageGenerationEnabled: boolean,
	hasUserImageGenerationAccess: boolean
) => {
	const meta = model?.info?.meta ?? {};
	const capabilities = meta.capabilities ?? {};

	if (
		!globalImageGenerationEnabled ||
		!hasUserImageGenerationAccess ||
		!capabilities.image_generation
	) {
		return false;
	}

	if (Array.isArray(meta.defaultFeatureIds)) {
		return meta.defaultFeatureIds.includes('image_generation');
	}

	return meta.builtinTools?.image_generation === true;
};

export const resolveImageGenerationFeature = (
	model: any,
	globalImageGenerationEnabled: boolean,
	hasUserImageGenerationAccess: boolean,
	imageGenerationEnabled: boolean,
	imageGenerationUserOverride: boolean | null
) => {
	const canUseImageGeneration = shouldEnableImageGenerationByDefault(
		model,
		globalImageGenerationEnabled,
		hasUserImageGenerationAccess
	);

	if (!canUseImageGeneration) {
		return false;
	}

	if (typeof imageGenerationUserOverride === 'boolean') {
		return imageGenerationUserOverride;
	}

	return imageGenerationEnabled || canUseImageGeneration;
};

export const resolveImageGenerationDraftState = (
	input: any,
	model: any,
	globalImageGenerationEnabled: boolean,
	hasUserImageGenerationAccess: boolean
) => {
	const legacyEnabled = input?.imageGenerationEnabled === true;
	const userOverride =
		typeof input?.imageGenerationUserOverride === 'boolean'
			? input.imageGenerationUserOverride
			: legacyEnabled
				? true
				: null;

	return {
		enabled: resolveImageGenerationFeature(
			model,
			globalImageGenerationEnabled,
			hasUserImageGenerationAccess,
			legacyEnabled,
			userOverride
		),
		userOverride
	};
};

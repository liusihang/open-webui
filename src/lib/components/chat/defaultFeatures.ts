export const canUseImageGeneration = (
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

	return true;
};

export const shouldEnableImageGenerationByDefault = (
	model: any,
	globalImageGenerationEnabled: boolean,
	hasUserImageGenerationAccess: boolean
) => {
	const meta = model?.info?.meta ?? {};

	if (!canUseImageGeneration(model, globalImageGenerationEnabled, hasUserImageGenerationAccess)) {
		return false;
	}

	if (Array.isArray(meta.defaultFeatureIds)) {
		return meta.defaultFeatureIds.includes('image_generation');
	}

	return false;
};

export const resolveImageGenerationFeature = (
	model: any,
	globalImageGenerationEnabled: boolean,
	hasUserImageGenerationAccess: boolean,
	imageGenerationEnabled: boolean,
	imageGenerationUserOverride: boolean | null
) => {
	if (!canUseImageGeneration(model, globalImageGenerationEnabled, hasUserImageGenerationAccess)) {
		return false;
	}

	if (typeof imageGenerationUserOverride === 'boolean') {
		return imageGenerationUserOverride;
	}

	return (
		imageGenerationEnabled ||
		shouldEnableImageGenerationByDefault(
			model,
			globalImageGenerationEnabled,
			hasUserImageGenerationAccess
		)
	);
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

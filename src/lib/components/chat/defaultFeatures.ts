export const shouldEnableImageGenerationByDefault = (
	model: any,
	globalImageGenerationEnabled: boolean,
	hasUserImageGenerationAccess: boolean
) => {
	const meta = model?.info?.meta ?? {};
	const capabilities = meta.capabilities ?? {};

	if (!globalImageGenerationEnabled || !hasUserImageGenerationAccess || !capabilities.image_generation) {
		return false;
	}

	if (Array.isArray(meta.defaultFeatureIds)) {
		return meta.defaultFeatureIds.includes('image_generation');
	}

	return meta.builtinTools?.image_generation === true;
};

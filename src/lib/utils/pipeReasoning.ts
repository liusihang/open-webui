// Pipe-only controls. Native models continue to use upstream ModelControls.
export const PIPE_REASONING_EFFORTS = ['low', 'medium', 'high', 'xhigh'] as const;
export type PipeReasoningEffort = (typeof PIPE_REASONING_EFFORTS)[number];

type PipeModel = {
	id?: string;
	info?: {
		base_model_id?: string;
		meta?: { capabilities?: { reasoning_effort?: unknown } };
	};
};

export const getRetainedPipeId = (
	model: PipeModel | undefined
): 'bifrostapi' | 'sub2api' | null => {
	// Custom model aliases use the underlying Pipe's base_model_id.
	const id = model?.info?.base_model_id || model?.id || '';
	const prefix = id.split('.')[0];
	return prefix === 'bifrostapi' || prefix === 'sub2api' ? prefix : null;
};

export const getPipeReasoningEfforts = (model: PipeModel | undefined): PipeReasoningEffort[] => {
	const pipe = getRetainedPipeId(model);
	if (!pipe) return [];
	const configured = model?.info?.meta?.capabilities?.reasoning_effort;
	if (Array.isArray(configured)) {
		return [
			...new Set(
				configured.filter((value): value is PipeReasoningEffort =>
					PIPE_REASONING_EFFORTS.includes(value as PipeReasoningEffort)
				)
			)
		];
	}
	// This is the existing Live Bifrost contract, not a model-name inference.
	// Sub2API includes both chat and messages routes; opt in per model instead
	// of showing an effort selector that its messages route would silently ignore.
	return pipe === 'bifrostapi' ? [...PIPE_REASONING_EFFORTS] : [];
};

export const buildPipeReasoningPayload = (model: PipeModel | undefined, value: unknown) => {
	if (!getPipeReasoningEfforts(model).includes(value as PipeReasoningEffort)) return {};
	return getRetainedPipeId(model) === 'bifrostapi'
		? { reasoning: { enabled: true, effort: value as PipeReasoningEffort } }
		: { reasoning_effort: value as PipeReasoningEffort };
};

export const chatUploadMetadata = (modelIds: string[], language?: string) => ({
	upload_context: 'chat',
	model_ids: [...new Set(modelIds.filter(Boolean))],
	...(language ? { language } : {})
});

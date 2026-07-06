export type ReasoningDepth = 'medium' | 'deep' | 'divergent';
export type ReasoningEffort = 'medium' | 'high' | 'xhigh';

export const getReasoningMaxTokens = (depth: ReasoningDepth): number => {
	if (depth === 'deep') {
		return 8126;
	}

	if (depth === 'divergent') {
		return 12400;
	}

	return 2048;
};

export const getReasoningEffort = (depth: ReasoningDepth): ReasoningEffort => {
	if (depth === 'deep') {
		return 'high';
	}

	if (depth === 'divergent') {
		return 'xhigh';
	}

	return 'medium';
};

export const buildReasoningPayload = (depth: ReasoningDepth) => ({
	enabled: true,
	effort: getReasoningEffort(depth),
	max_tokens: getReasoningMaxTokens(depth)
});

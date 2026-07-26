type AgentModeConfig = {
	enable_agent_mode?: boolean;
	features?: Record<string, unknown> & {
		enable_agent_mode?: boolean;
	};
};

export type ReasoningDepth = 'medium' | 'deep' | 'divergent';
export type ReasoningEffort = 'medium' | 'high' | 'xhigh';
export type ConversationMode = 'chat' | 'agent';

export const isAgentModeCapabilityEnabled = (config: AgentModeConfig | undefined): boolean =>
	config?.features?.enable_agent_mode === true || config?.enable_agent_mode === true;

export const isAgentModeRequestConstraintEnabled = isAgentModeCapabilityEnabled;

export const normalizeConversationMode = (value: unknown): ConversationMode =>
	value === 'agent' ? 'agent' : 'chat';

export const resolveConversationModeRequestModels = (
	selectedModels: string[],
	conversationMode: ConversationMode
): string[] => {
	if (conversationMode !== 'agent') {
		return selectedModels;
	}

	const leaderModelId = selectedModels.find((modelId) => modelId !== '');
	return leaderModelId ? [leaderModelId] : [''];
};

export const resolveAgentModeRequestModels = (
	selectedModels: string[],
	config: AgentModeConfig | undefined
): string[] => {
	if (!isAgentModeCapabilityEnabled(config)) {
		return selectedModels;
	}

	return resolveConversationModeRequestModels(selectedModels, 'agent');
};

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

export const buildConversationModeReasoningPayload = (
	_conversationMode: ConversationMode,
	depth: ReasoningDepth
) => buildReasoningPayload(depth);

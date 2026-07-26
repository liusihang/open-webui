type AgentModeConfig = {
	enable_agent_mode?: boolean;
	features?: Record<string, unknown> & {
		enable_agent_mode?: boolean;
	};
};

export type ReasoningEffort = 'low' | 'medium' | 'high' | 'xhigh';
export type ReasoningDepth = ReasoningEffort | 'deep' | 'divergent';
export type ConversationMode = 'chat' | 'agent';

export const REASONING_EFFORTS: ReasoningEffort[] = ['low', 'medium', 'high', 'xhigh'];

const isReasoningEffort = (value: unknown): value is ReasoningEffort =>
	REASONING_EFFORTS.includes(value as ReasoningEffort);

export const isAgentModeCapabilityEnabled = (config: AgentModeConfig | undefined): boolean =>
	config?.features?.enable_agent_mode === true || config?.enable_agent_mode === true;

export const isAgentModeRequestConstraintEnabled = isAgentModeCapabilityEnabled;

export const normalizeConversationMode = (value: unknown): ConversationMode =>
	value === 'agent' ? 'agent' : 'chat';

export const resolveConversationModeRequestModels = (
	selectedModels: string[],
	_conversationMode: ConversationMode
): string[] => {
	const selectedModelId = selectedModels.find((modelId) => modelId !== '');
	return selectedModelId ? [selectedModelId] : [''];
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

export const normalizeReasoningEffort = (value: unknown): ReasoningEffort => {
	if (isReasoningEffort(value)) {
		return value;
	}

	if (value === 'deep') {
		return 'high';
	}

	if (value === 'divergent') {
		return 'xhigh';
	}

	return 'medium';
};

export const resolveModelReasoningEfforts = (model: unknown): ReasoningEffort[] => {
	const modelRecord = model as
		| {
				id?: unknown;
				info?: {
					meta?: {
						capabilities?: {
							reasoning_effort?: unknown;
						};
					};
				};
		  }
		| undefined;
	const configuredEfforts = modelRecord?.info?.meta?.capabilities?.reasoning_effort;

	if (Array.isArray(configuredEfforts)) {
		return configuredEfforts.filter(isReasoningEffort);
	}

	const modelId = typeof modelRecord?.id === 'string' ? modelRecord.id : '';
	if (modelId === 'bifrostapi' || modelId.startsWith('bifrostapi.')) {
		return [...REASONING_EFFORTS];
	}

	return [];
};

export const getReasoningEffort = (depth: ReasoningDepth): ReasoningEffort =>
	normalizeReasoningEffort(depth);

export const buildReasoningPayload = (depth: ReasoningDepth) => ({
	enabled: true,
	effort: getReasoningEffort(depth)
});

export const buildModelReasoningPayload = (model: unknown, effort: ReasoningDepth) => {
	const allowedEfforts = resolveModelReasoningEfforts(model);
	if (allowedEfforts.length === 0) {
		return undefined;
	}

	const normalizedEffort = normalizeReasoningEffort(effort);
	const selectedEffort = allowedEfforts.includes(normalizedEffort)
		? normalizedEffort
		: allowedEfforts.includes('medium')
			? 'medium'
			: allowedEfforts[0];

	return buildReasoningPayload(selectedEffort);
};

export const buildConversationModeReasoningPayload = (
	_conversationMode: ConversationMode,
	depth: ReasoningDepth
) => buildReasoningPayload(depth);

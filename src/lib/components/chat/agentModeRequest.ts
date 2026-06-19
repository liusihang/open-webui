type AgentModeConfig = {
	enable_agent_mode?: boolean;
	features?: Record<string, unknown> & {
		enable_agent_mode?: boolean;
	};
};

export const isAgentModeRequestConstraintEnabled = (config: AgentModeConfig | undefined): boolean =>
	config?.features?.enable_agent_mode === true || config?.enable_agent_mode === true;

export const resolveAgentModeRequestModels = (
	selectedModels: string[],
	_config: AgentModeConfig | undefined
): string[] => {
	const leaderModelId = selectedModels.find((modelId) => modelId !== '');
	return leaderModelId ? [leaderModelId] : [''];
};

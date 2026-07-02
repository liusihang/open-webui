import type { AgentRunState } from './types';

type AgentRunMessage = {
	done?: boolean;
	agent_run_id?: string;
};

const TERMINAL_RUN_STATUSES = new Set<AgentRunState>([
	'completed',
	'failed',
	'cancelled',
	'budget_exceeded'
]);

export const isTerminalAgentRunStatus = (status: AgentRunState): boolean =>
	TERMINAL_RUN_STATUSES.has(status);

export const markAgentRunMessageDone = (
	message: AgentRunMessage | undefined,
	status: AgentRunState
): boolean => {
	if (!message?.agent_run_id || !isTerminalAgentRunStatus(status)) {
		return false;
	}

	if (message.done === true) {
		return false;
	}

	message.done = true;
	return true;
};

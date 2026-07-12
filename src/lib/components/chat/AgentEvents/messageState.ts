import type { AgentRunState } from './types';

type AgentRunMessage = {
	done?: boolean;
	agent_run_id?: string;
};

type AgentRunHistoryMessage = AgentRunMessage & {
	role?: string;
};

type AgentRunHistory = {
	currentId?: string | null;
	messages?: Record<string, AgentRunHistoryMessage>;
};

export type AgentRunStopOutcome =
	| 'agent_cancel_requested'
	| 'agent_cancel_pending'
	| 'legacy_stopped';

export type AgentRunStopControlState = {
	visible: boolean;
	disabled: boolean;
	ariaBusy: boolean;
	agentRunId: string | null;
};

export type AgentRunStopController = {
	getControlState: (options: {
		history: AgentRunHistory | null | undefined;
		isActive: boolean;
		recording: boolean;
		prompt: string;
		hasFiles: boolean;
	}) => AgentRunStopControlState;
	isRunPending: (runId: string | null) => boolean;
	requestStop: (history: AgentRunHistory | null | undefined) => Promise<AgentRunStopOutcome>;
	subscribe: (listener: () => void) => () => void;
	syncHistory: (history: AgentRunHistory | null | undefined) => void;
};

const TERMINAL_RUN_STATUSES = new Set<AgentRunState>([
	'completed',
	'failed',
	'cancelled',
	'budget_exceeded'
]);

export const AGENT_RUN_PENDING_REGISTRY_LIMIT = 128;

const pendingAgentRunRegistry = new Map<string, { messageId: string }>();
const pendingAgentRunListeners = new Set<() => void>();

const notifyPendingAgentRunListeners = () => {
	for (const listener of pendingAgentRunListeners) {
		listener();
	}
};

const deletePendingAgentRun = (runId: string): boolean => pendingAgentRunRegistry.delete(runId);

const registerPendingAgentRun = (runId: string, messageId: string) => {
	if (pendingAgentRunRegistry.size >= AGENT_RUN_PENDING_REGISTRY_LIMIT) {
		const oldestRunId = pendingAgentRunRegistry.keys().next().value;
		if (oldestRunId) {
			pendingAgentRunRegistry.delete(oldestRunId);
		}
	}

	pendingAgentRunRegistry.set(runId, { messageId });
	notifyPendingAgentRunListeners();
};

export const isTerminalAgentRunStatus = (status: AgentRunState): boolean =>
	TERMINAL_RUN_STATUSES.has(status);

export const getActiveAgentRunId = (history: AgentRunHistory | null | undefined): string | null => {
	if (!history?.currentId) {
		return null;
	}

	const message = history.messages?.[history.currentId];
	if (message?.role !== 'assistant' || message.done !== false || !message.agent_run_id) {
		return null;
	}

	return message.agent_run_id;
};

export const getAgentRunStopAriaLabel = (
	state: AgentRunStopControlState,
	translate: (key: 'Stop' | 'Pending') => string
): string => {
	const stopLabel = translate('Stop');
	return state.ariaBusy ? `${stopLabel}: ${translate('Pending')}` : stopLabel;
};

export const createAgentRunStopController = ({
	cancelAgentRun,
	stopResponse
}: {
	cancelAgentRun: (runId: string) => Promise<unknown>;
	stopResponse: () => Promise<unknown>;
}): AgentRunStopController => {
	const syncHistory = (history: AgentRunHistory | null | undefined) => {
		if (pendingAgentRunRegistry.size === 0) {
			return;
		}

		let changed = false;
		for (const [runId, entry] of pendingAgentRunRegistry) {
			const message = history?.messages?.[entry.messageId];
			if (message?.agent_run_id === runId && message.done === true) {
				changed = deletePendingAgentRun(runId) || changed;
			}
		}
		if (changed) {
			notifyPendingAgentRunListeners();
		}
	};

	return {
		getControlState: ({ history, isActive, recording, prompt, hasFiles }) => {
			syncHistory(history);
			const agentRunId = getActiveAgentRunId(history);
			const ariaBusy = agentRunId !== null && pendingAgentRunRegistry.has(agentRunId);
			return {
				visible: !recording && isActive && prompt === '' && !hasFiles,
				disabled: ariaBusy,
				ariaBusy,
				agentRunId
			};
		},
		isRunPending: (runId) => runId !== null && pendingAgentRunRegistry.has(runId),
		requestStop: async (history) => {
			syncHistory(history);
			const agentRunId = getActiveAgentRunId(history);
			if (!agentRunId) {
				await stopResponse();
				return 'legacy_stopped';
			}
			if (pendingAgentRunRegistry.has(agentRunId)) {
				return 'agent_cancel_pending';
			}

			registerPendingAgentRun(agentRunId, history?.currentId ?? '');
			try {
				await cancelAgentRun(agentRunId);
				return 'agent_cancel_requested';
			} catch (error) {
				if (deletePendingAgentRun(agentRunId)) {
					notifyPendingAgentRunListeners();
				}
				throw error;
			}
		},
		subscribe: (listener) => {
			pendingAgentRunListeners.add(listener);
			return () => {
				pendingAgentRunListeners.delete(listener);
			};
		},
		syncHistory
	};
};

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

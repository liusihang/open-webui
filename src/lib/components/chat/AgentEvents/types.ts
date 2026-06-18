export type AgentRunState =
	| 'queued'
	| 'running'
	| 'waiting_approval'
	| 'finalizing'
	| 'completed'
	| 'failed'
	| 'cancelled'
	| 'budget_exceeded';

export const AGENT_RUN_EVENT_TYPES = [
	'run.queued',
	'run.running',
	'action.summary',
	'tool.requested',
	'tool.started',
	'tool.completed',
	'tool.failed',
	'approval.requested',
	'approval.completed',
	'artifact.registered',
	'subagent.created',
	'subagent.updated',
	'subagent.completed',
	'subagent.failed',
	'model.selection.requested',
	'model.selection.completed',
	'final.started',
	'final.delta',
	'run.completed',
	'run.failed',
	'run.cancelled',
	'run.budget_exceeded'
] as const;

export type AgentRunEventType = (typeof AGENT_RUN_EVENT_TYPES)[number];

export type AgentRun = {
	id: string;
	user_id?: string;
	chat_id?: string | null;
	user_message_id?: string | null;
	assistant_message_id?: string | null;
	state: AgentRunState;
	state_version?: number;
	leader_model_id?: string | null;
	runtime_session_id?: string | null;
	participants?: Record<string, unknown> | null;
	summary?: Record<string, unknown> | null;
	error?: Record<string, unknown> | null;
	created_at: number;
	updated_at?: number;
	started_at?: number | null;
	ended_at?: number | null;
};

export type AgentRunEventPayload = Record<string, unknown>;

export type AgentRunEventCategory =
	| 'run'
	| 'action'
	| 'tool'
	| 'approval'
	| 'artifact'
	| 'subagent'
	| 'model'
	| 'final';

export type AgentRunEventMetadata = {
	label: string;
	value: string;
};

export type AgentRunEvent = {
	id?: string;
	run_id: string;
	agent_run_id?: string;
	chat_id?: string | null;
	assistant_message_id?: string | null;
	seq: number;
	event_type: AgentRunEventType;
	participant_id?: string | null;
	phase?: string | null;
	summary?: string | null;
	payload: AgentRunEventPayload;
	created_at: number;
};

export type AgentRunEventViewItem = {
	seq: number;
	eventType: AgentRunEventType;
	category: AgentRunEventCategory;
	label: string;
	participantId: string | null;
	phase: string | null;
	summary: string;
	metadata: AgentRunEventMetadata[];
	details: AgentRunEventPayload | null;
	status: 'running' | 'done' | 'error';
	createdAt: number;
};

export type AgentRunEventState = {
	items: AgentRunEventViewItem[];
	lastSeq: number;
	runStatus: AgentRunState;
	runStatusSeq: number;
	counts: Record<AgentRunEventCategory, number>;
	finalText: string;
	finalStarted: boolean;
	seenSeqs: Set<number>;
	seenFinalDeltaKeys: Set<string>;
	finalDeltaChunks: Map<
		string,
		{
			streamId: string;
			deltaIndex: number;
			text: string;
		}
	>;
};

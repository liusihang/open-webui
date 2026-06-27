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
	'text.delta',
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

export type AgentTextBlockKind = 'assistant_note' | 'action_summary' | 'legacy';

export type AgentRunTextBlock = {
	id: string;
	kind: AgentTextBlockKind;
	participantId: string | null;
	phase: string | null;
	text: string;
	status: 'running' | 'done';
	firstSeq: number;
	lastSeq: number;
	createdAt: number;
};

export type AgentTranscriptToolStatus = 'running' | 'done' | 'error';

export type AgentTranscriptApprovalStatus = 'pending' | 'approved' | 'rejected';

export type AgentTranscriptPartBase = {
	seq: number;
	createdAt: number;
	participantId: string | null;
	phase: string | null;
	/** Whether the part should render expanded by default in the UI. */
	defaultExpanded: boolean;
};

export type AgentTranscriptTextPart = AgentTranscriptPartBase & {
	kind: 'assistant_note' | 'action_summary' | 'legacy_note';
	textKind: AgentTextBlockKind;
	blockId: string;
	text: string;
	status: 'running' | 'done';
};

export type AgentTranscriptToolPart = AgentTranscriptPartBase & {
	kind: 'tool';
	toolCallId: string;
	toolName: string | null;
	label: string;
	status: AgentTranscriptToolStatus;
	summary: string;
	metadata: AgentRunEventMetadata[];
	details: AgentRunEventPayload | null;
};

export type AgentTranscriptApprovalPart = AgentTranscriptPartBase & {
	kind: 'approval';
	approvalId: string;
	action: string | null;
	description: string;
	status: AgentTranscriptApprovalStatus;
	metadata: AgentRunEventMetadata[];
	details: AgentRunEventPayload | null;
};

export type AgentTranscriptArtifactPart = AgentTranscriptPartBase & {
	kind: 'artifact';
	artifactId: string;
	name: string | null;
	path: string | null;
	mimeType: string | null;
	summary: string;
	details: AgentRunEventPayload | null;
};

export type AgentTranscriptErrorPart = AgentTranscriptPartBase & {
	kind: 'error';
	label: string;
	summary: string;
	details: AgentRunEventPayload | null;
};

export type AgentTranscriptSubagentPart = AgentTranscriptPartBase & {
	kind: 'subagent';
	participantName: string | null;
	label: string;
	status: 'running' | 'done' | 'error';
	summary: string;
	resultSummary: string | null;
	details: AgentRunEventPayload | null;
};

export type AgentTranscriptRunPart = AgentTranscriptPartBase & {
	kind: 'run';
	label: string;
	summary: string;
	runStatus: AgentRunState;
};

export type AgentTranscriptModelPart =
	| AgentTranscriptTextPart
	| AgentTranscriptToolPart
	| AgentTranscriptApprovalPart
	| AgentTranscriptArtifactPart
	| AgentTranscriptErrorPart
	| AgentTranscriptSubagentPart
	| AgentTranscriptRunPart;

export type AgentTranscriptSummary = {
	toolCount: number;
	artifactCount: number;
	approvalCount: number;
	subagentCount: number;
	hasError: boolean;
	hasPendingApproval: boolean;
};

export type AgentTranscriptModel = {
	runStatus: AgentRunState;
	isRunning: boolean;
	isTerminal: boolean;
	parts: AgentTranscriptModelPart[];
	final: { content: string; done: boolean } | null;
	summary: AgentTranscriptSummary;
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
			seq: number;
		}
	>;
	textBlocks: AgentRunTextBlock[];
	seenTextDeltaKeys: Set<string>;
	textDeltaChunks: Map<
		string,
		{
			blockId: string;
			deltaIndex: number;
			text: string;
			seq: number;
			kind: AgentTextBlockKind;
			participantId: string | null;
			phase: string | null;
			createdAt: number;
		}
	>;
};

import type { AgentRun, AgentRunEvent, AgentRunEventType } from './types';

export const agentRunFixture = (overrides: Partial<AgentRun> = {}): AgentRun => ({
	id: 'run-1',
	user_id: 'user-1',
	chat_id: 'chat-1',
	user_message_id: 'message-user-1',
	assistant_message_id: 'message-assistant-1',
	state: 'running',
	state_version: 1,
	leader_model_id: 'model-1',
	runtime_session_id: 'runtime-session-1',
	participants: null,
	summary: null,
	error: null,
	created_at: 1_718_000_000,
	updated_at: 1_718_000_000,
	started_at: 1_718_000_001,
	ended_at: null,
	...overrides
});

export const agentRunEventFixture = (
	overrides: Partial<AgentRunEvent> & { event_type?: AgentRunEventType } = {}
): AgentRunEvent => ({
	id: `event-${overrides.seq ?? 1}`,
	run_id: 'run-1',
	agent_run_id: 'run-1',
	chat_id: 'chat-1',
	assistant_message_id: 'message-assistant-1',
	seq: 1,
	event_type: 'action.summary',
	participant_id: 'leader',
	phase: null,
	summary: 'Working',
	payload: {},
	created_at: 1_718_000_000,
	...overrides
});

<script lang="ts">
	import { createEventDispatcher, onMount } from 'svelte';

	import { createAgentRunEventsSource, getAgentRunEvents } from '$lib/apis/agentRuns';

	import { foldAgentEventIntoStatusHistory, type AgentStatusEntry } from './agentStatusAdapter';
	import { createAgentRunEventConnection } from './agentRunEventConnection';
	import { createAgentRunEventState, foldAgentRunEvent } from './eventFold';
	import { buildAgentTranscriptModel } from './transcriptModel';
	import { isTerminalAgentRunStatus } from './messageState';
	import {
		type AgentConnectionState,
		type AgentRunEvent,
		type AgentRunState,
		type AgentTranscriptModel
	} from './types';

	export let agentRunId: string;
	export let statusHistory: AgentStatusEntry[] = [];

	const dispatch = createEventDispatcher<{
		final: { content: string; done: boolean; status: AgentRunState };
		terminal: { agentRunId: string; runStatus: AgentRunState };
		transcript: { model: AgentTranscriptModel };
	}>();

	let state = createAgentRunEventState();
	let dispatchedTerminalStatus: AgentRunState | null = null;
	let started = false;
	let lastTranscriptSignature = '';
	let connectionState: AgentConnectionState = 'connected';

	const emitTranscript = () => {
		const model = buildAgentTranscriptModel(state, connectionState);
		const signature =
			state.lastSeq +
			':' +
			model.parts.length +
			':' +
			state.runStatus +
			':' +
			state.finalText.length +
			':' +
			connectionState;
		if (signature === lastTranscriptSignature) {
			return;
		}
		lastTranscriptSignature = signature;
		dispatch('transcript', { model });
	};

	const setConnectionState = (next: AgentConnectionState) => {
		if (connectionState === next) {
			return;
		}
		connectionState = next;
		emitTranscript();
	};

	const ingestEvent = (event: AgentRunEvent) => {
		if (state.seenSeqs.has(event.seq)) {
			return;
		}

		state = foldAgentRunEvent(state, event);
		statusHistory = foldAgentEventIntoStatusHistory(statusHistory, event);

		if (state.finalText) {
			dispatch('final', {
				content: state.finalText,
				done: isTerminalAgentRunStatus(state.runStatus),
				status: state.runStatus
			});
		}

		emitTranscript();
	};

	onMount(() => {
		if (!agentRunId) return;
		started = true;
		setConnectionState('connected');

		return createAgentRunEventConnection({
			runId: agentRunId,
			getAfterSeq: () => state.lastSeq,
			getEvents: (runId, afterSeq) =>
				getAgentRunEvents(localStorage.getItem('token') ?? '', runId, { afterSeq }),
			createSource: (runId, afterSeq) => createAgentRunEventsSource(runId, { afterSeq }),
			onEvent: ingestEvent,
			isTerminal: () => isTerminalAgentRunStatus(state.runStatus),
			onConnectionState: setConnectionState
		});
	});

	$: if (
		started &&
		isTerminalAgentRunStatus(state.runStatus) &&
		dispatchedTerminalStatus !== state.runStatus
	) {
		dispatchedTerminalStatus = state.runStatus;
		setConnectionState('connected');
		dispatch('terminal', { agentRunId, runStatus: state.runStatus });
	}
</script>

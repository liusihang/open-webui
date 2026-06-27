<script lang="ts">
	import { createEventDispatcher, onDestroy, onMount } from 'svelte';

	import { createAgentRunEventsSource, getAgentRunEvents } from '$lib/apis/agentRuns';

	import { foldAgentEventIntoStatusHistory, type AgentStatusEntry } from './agentStatusAdapter';
	import { createAgentRunEventState, foldAgentRunEvent } from './eventFold';
	import { buildAgentTranscriptModel } from './transcriptModel';
	import { isTerminalAgentRunStatus } from './messageState';
	import {
		AGENT_RUN_EVENT_TYPES,
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
	let source: EventSource | null = null;
	let dispatchedTerminalStatus: AgentRunState | null = null;
	let started = false;
	let lastTranscriptSignature = '';

	const emitTranscript = () => {
		const model = buildAgentTranscriptModel(state);
		const signature = `${state.lastSeq}:${model.parts.length}:${state.runStatus}:${state.finalText.length}`;
		if (signature === lastTranscriptSignature) {
			return;
		}
		lastTranscriptSignature = signature;
		dispatch('transcript', { model });
	};

	const ingestEvent = (event: AgentRunEvent) => {
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

	const handleMessage = (event: Event) => {
		const message = event as MessageEvent<string>;
		try {
			const parsed = JSON.parse(message.data) as AgentRunEvent;
			ingestEvent(parsed);
		} catch {
			// Ignore malformed payloads; the SSE stream keeps going.
		}
	};

	onMount(() => {
		let cancelled = false;

		const start = async () => {
			if (!agentRunId) {
				return;
			}

			started = true;

			try {
				const events = await getAgentRunEvents(
					localStorage.getItem('token') ?? '',
					agentRunId,
					{ afterSeq: state.lastSeq }
				);
				if (cancelled) {
					return;
				}

				for (const event of events) {
					ingestEvent(event);
				}

				if (isTerminalAgentRunStatus(state.runStatus)) {
					return;
				}

				source = createAgentRunEventsSource(agentRunId, { afterSeq: state.lastSeq });
				source.onmessage = handleMessage;
				for (const eventType of AGENT_RUN_EVENT_TYPES) {
					source.addEventListener(eventType, handleMessage);
				}
				source.onerror = () => {
					if (isTerminalAgentRunStatus(state.runStatus)) {
						source?.close();
					}
				};
			} catch {
				// Network or auth failures are surfaced via the terminal dispatcher
				// once the run reaches a terminal state via backfill/reconnect.
			}
		};

		void start();

		return () => {
			cancelled = true;
			source?.close();
		};
	});

	onDestroy(() => {
		source?.close();
	});

	$: if (
		started &&
		isTerminalAgentRunStatus(state.runStatus) &&
		dispatchedTerminalStatus !== state.runStatus
	) {
		dispatchedTerminalStatus = state.runStatus;
		source?.close();
		dispatch('terminal', { agentRunId, runStatus: state.runStatus });
	}
</script></script>

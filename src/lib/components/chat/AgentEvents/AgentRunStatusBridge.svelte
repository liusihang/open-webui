<script lang="ts">
	import { createEventDispatcher, onDestroy, onMount } from 'svelte';

	import { createAgentRunEventsSource, getAgentRunEvents } from '$lib/apis/agentRuns';

	import { foldAgentEventIntoStatusHistory, type AgentStatusEntry } from './agentStatusAdapter';
	import { createAgentRunEventState, foldAgentRunEvent } from './eventFold';
	import { buildAgentTranscriptModel } from './transcriptModel';
	import { isTerminalAgentRunStatus } from './messageState';
	import {
		AGENT_RUN_EVENT_TYPES,
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
	let source: EventSource | null = null;
	let dispatchedTerminalStatus: AgentRunState | null = null;
	let started = false;
	let lastTranscriptSignature = '';
	let connectionState: AgentConnectionState = 'connected';
	let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
	let reconnectAttempts = 0;
	let cancelled = false;

	const MAX_RECONNECT_DELAY_MS = 15_000;
	const BASE_RECONNECT_DELAY_MS = 1_000;

	const clearReconnectTimer = () => {
		if (reconnectTimer !== null) {
			clearTimeout(reconnectTimer);
			reconnectTimer = null;
		}
	};

	const reconnectDelay = () => {
		const delay = Math.min(
			BASE_RECONNECT_DELAY_MS * 2 ** reconnectAttempts,
			MAX_RECONNECT_DELAY_MS
		);
		return delay + Math.floor(Math.random() * 500);
	};

	const setConnectionState = (next: AgentConnectionState) => {
		if (connectionState === next) {
			return;
		}
		connectionState = next;
		emitTranscript();
	};

	const emitTranscript = () => {
		const model = buildAgentTranscriptModel(state, connectionState);
		const signature = `${state.lastSeq}:${model.parts.length}:${state.runStatus}:${state.finalText.length}:${connectionState}`;
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

	const closeSource = () => {
		if (source) {
			source.onmessage = null;
			source.onerror = null;
			for (const eventType of AGENT_RUN_EVENT_TYPES) {
				source.removeEventListener(eventType, handleMessage);
			}
			source.close();
			source = null;
		}
	};

	const openStream = () => {
		closeSource();

		source = createAgentRunEventsSource(agentRunId, { afterSeq: state.lastSeq });
		source.onmessage = handleMessage;
		for (const eventType of AGENT_RUN_EVENT_TYPES) {
			source.addEventListener(eventType, handleMessage);
		}
		source.onerror = () => {
			closeSource();

			if (isTerminalAgentRunStatus(state.runStatus)) {
				setConnectionState('connected');
				return;
			}

			setConnectionState('disconnected');
			scheduleReconnect();
		};
	};

	const scheduleReconnect = () => {
		clearReconnectTimer();
		if (cancelled || isTerminalAgentRunStatus(state.runStatus)) {
			return;
		}

		reconnectAttempts += 1;
		const delay = reconnectDelay();
		setConnectionState('reconnecting');

		reconnectTimer = setTimeout(async () => {
			reconnectTimer = null;
			if (cancelled || isTerminalAgentRunStatus(state.runStatus)) {
				return;
			}

			try {
				const events = await getAgentRunEvents(localStorage.getItem('token') ?? '', agentRunId, {
					afterSeq: state.lastSeq
				});
				if (cancelled) {
					return;
				}

				for (const event of events) {
					ingestEvent(event);
				}

				if (isTerminalAgentRunStatus(state.runStatus)) {
					setConnectionState('connected');
					return;
				}

				reconnectAttempts = 0;
				setConnectionState('connected');
				openStream();
			} catch {
				if (!cancelled && !isTerminalAgentRunStatus(state.runStatus)) {
					scheduleReconnect();
				}
			}
		}, delay);
	};

	onMount(() => {
		cancelled = false;

		const start = async () => {
			if (!agentRunId) {
				return;
			}

			started = true;

			try {
				setConnectionState('connected');
				const events = await getAgentRunEvents(localStorage.getItem('token') ?? '', agentRunId, {
					afterSeq: state.lastSeq
				});
				if (cancelled) {
					return;
				}

				for (const event of events) {
					ingestEvent(event);
				}

				if (isTerminalAgentRunStatus(state.runStatus)) {
					return;
				}

				openStream();
			} catch {
				// Network or auth failures are surfaced via the terminal dispatcher
				// once the run reaches a terminal state via backfill/reconnect.
				if (!isTerminalAgentRunStatus(state.runStatus)) {
					scheduleReconnect();
				}
			}
		};

		void start();

		return () => {
			cancelled = true;
			clearReconnectTimer();
			closeSource();
		};
	});

	onDestroy(() => {
		cancelled = true;
		clearReconnectTimer();
		closeSource();
	});

	$: if (
		started &&
		isTerminalAgentRunStatus(state.runStatus) &&
		dispatchedTerminalStatus !== state.runStatus
	) {
		dispatchedTerminalStatus = state.runStatus;
		clearReconnectTimer();
		closeSource();
		setConnectionState('connected');
		dispatch('terminal', { agentRunId, runStatus: state.runStatus });
	}
</script>

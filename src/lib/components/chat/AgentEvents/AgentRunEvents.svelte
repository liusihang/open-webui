<script lang="ts">
	import { onDestroy, onMount } from 'svelte';

	import { createAgentRunEventsSource, getAgentRunEvents } from '$lib/apis/agentRuns';

	import AgentFinalAnswer from './AgentFinalAnswer.svelte';
	import AgentRunHeader from './AgentRunHeader.svelte';
	import AgentRunTimeline from './AgentRunTimeline.svelte';
	import { createAgentRunEventState } from './eventFold';
	import { createAgentRunRenderModel, type AgentRunTransportStatus } from './renderModel';
	import { createAgentRunEventsStore } from './store';
	import { AGENT_RUN_EVENT_TYPES, type AgentRunEvent, type AgentRunEventState } from './types';

	export let agentRunId: string;
	export let showFinalText = true;

	const eventsStore = createAgentRunEventsStore();
	let state: AgentRunEventState = createAgentRunEventState();
	let expandedGroupIds = new Set<string>();
	let streamStatus: AgentRunTransportStatus = 'loading';
	let streamError = '';

	const syncExpandedGroups = (nextState: AgentRunEventState) => {
		const nextModel = createAgentRunRenderModel(nextState, { transportStatus: streamStatus });
		const nextExpandedGroupIds = new Set(expandedGroupIds);

		for (const group of nextModel.groups) {
			if (
				group.status === 'error' ||
				group.status === 'waiting' ||
				(group.kind === 'tool' && group.status === 'running')
			) {
				nextExpandedGroupIds.add(group.id);
			}
		}

		expandedGroupIds = nextExpandedGroupIds;
	};

	const unsubscribe = eventsStore.subscribe((nextState) => {
		state = nextState;
		syncExpandedGroups(nextState);
	});

	const setGroupOpen = (id: string, open: boolean) => {
		const nextExpandedGroupIds = new Set(expandedGroupIds);
		if (open) {
			nextExpandedGroupIds.add(id);
		} else {
			nextExpandedGroupIds.delete(id);
		}
		expandedGroupIds = nextExpandedGroupIds;
	};

	let source: EventSource | null = null;

	onMount(() => {
		let cancelled = false;

		const start = async () => {
			if (!agentRunId) {
				streamStatus = 'error';
				streamError = 'Missing Agent Run id';
				return;
			}

			try {
				const events = await getAgentRunEvents(localStorage.getItem('token') ?? '', agentRunId, {
					afterSeq: state.lastSeq
				});
				if (cancelled) {
					return;
				}

				eventsStore.backfill(events);
				source = createAgentRunEventsSource(agentRunId, { afterSeq: state.lastSeq });
				const handleMessage = (event: Event) => {
					const message = event as MessageEvent<string>;
					try {
						const parsed = JSON.parse(message.data) as AgentRunEvent;
						eventsStore.fold(parsed);
						streamStatus = 'live';
						streamError = '';
					} catch {
						streamStatus = 'error';
						streamError = 'Unable to parse Agent Run event';
					}
				};

				source.onopen = () => {
					streamStatus = 'live';
					streamError = '';
				};
				source.onerror = () => {
					streamStatus = state.items.length > 0 ? 'reconnecting' : 'error';
					streamError = state.items.length > 0 ? '' : 'Agent Event Stream disconnected';
				};
				source.onmessage = handleMessage;
				for (const eventType of AGENT_RUN_EVENT_TYPES) {
					source.addEventListener(eventType, handleMessage);
				}
			} catch (error) {
				if (!cancelled) {
					streamStatus = 'error';
					streamError = `${error}`;
				}
			}
		};

		void start();

		return () => {
			cancelled = true;
			source?.close();
		};
	});

	onDestroy(() => {
		unsubscribe();
		source?.close();
	});

	$: renderModel = createAgentRunRenderModel(state, { transportStatus: streamStatus });
</script>

<div
	class="agent-run-events my-2 flex w-full flex-col overflow-hidden rounded-lg border border-gray-200 bg-white text-sm shadow-sm dark:border-gray-800 dark:bg-gray-950/30"
>
	<AgentRunHeader model={renderModel} {streamError} />
	<AgentRunTimeline
		groups={renderModel.groups}
		artifacts={renderModel.artifacts}
		{expandedGroupIds}
		{setGroupOpen}
	/>

	{#if showFinalText && renderModel.finalAnswer}
		<AgentFinalAnswer {agentRunId} finalAnswer={renderModel.finalAnswer} />
	{/if}
</div>

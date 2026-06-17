<script lang="ts">
	import { onDestroy, onMount } from 'svelte';

	import { createAgentRunEventsSource, getAgentRunEvents } from '$lib/apis/agentRuns';
	import ContentRenderer from '$lib/components/chat/Messages/ContentRenderer.svelte';

	import { createAgentRunEventState } from './eventFold';
	import { createAgentRunEventsStore } from './store';
	import {
		AGENT_RUN_EVENT_TYPES,
		type AgentRunEvent,
		type AgentRunEventState,
		type AgentRunEventViewItem
	} from './types';

	export let agentRunId: string;
	export let showFinalText = true;

	const eventsStore = createAgentRunEventsStore();
	let state: AgentRunEventState = createAgentRunEventState();
	let streamStatus: 'loading' | 'live' | 'reconnecting' | 'error' = 'loading';
	let streamError = '';

	const unsubscribe = eventsStore.subscribe((nextState) => {
		state = nextState;
	});

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

	const detailsText = (details: AgentRunEventViewItem['details']) =>
		details ? JSON.stringify(details, null, 2) : '';

	const statusClass = (status: AgentRunEventViewItem['status']) => {
		if (status === 'error') {
			return 'border-red-300/70 bg-red-50/70 text-red-800 dark:border-red-800/60 dark:bg-red-950/30 dark:text-red-200';
		}
		if (status === 'running') {
			return 'border-amber-300/70 bg-amber-50/70 text-amber-900 dark:border-amber-700/60 dark:bg-amber-950/30 dark:text-amber-100';
		}

		return 'border-gray-200 bg-gray-50 text-gray-700 dark:border-gray-800 dark:bg-gray-900/40 dark:text-gray-200';
	};
</script>

<div class="agent-run-events my-2 flex w-full flex-col gap-2 text-sm">
	{#if state.items.length > 0}
		<div class="flex flex-col gap-1.5" aria-label="Agent run events">
			{#each state.items as item (item.seq)}
				<details
					class="group rounded-lg border px-3 py-2 transition {statusClass(item.status)}"
					open={item.status === 'error'}
				>
					<summary class="flex cursor-pointer list-none items-center justify-between gap-3">
						<span class="min-w-0 truncate font-medium">{item.summary}</span>
						<span class="shrink-0 text-[11px] uppercase opacity-70">
							{item.participantId ?? 'agent'}
						</span>
					</summary>

					{#if item.details}
						<pre
							class="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded-md border border-current/10 bg-white/65 p-2 text-xs leading-relaxed text-gray-800 dark:bg-black/20 dark:text-gray-100">{detailsText(
								item.details
							)}</pre>
					{/if}
				</details>
			{/each}
		</div>
	{/if}

	{#if showFinalText && state.finalText}
		<div class="agent-run-final-answer">
			<ContentRenderer
				id={`agent-run-final-${agentRunId}`}
				content={state.finalText}
				done={false}
				floatingButtons={false}
				save={false}
				preview={false}
			/>
		</div>
	{/if}

	{#if streamStatus === 'loading'}
		<div class="text-xs text-gray-500 dark:text-gray-400">Loading Agent Run events...</div>
	{:else if streamStatus === 'reconnecting'}
		<div class="text-xs text-amber-700 dark:text-amber-300">Reconnecting Agent Event Stream...</div>
	{:else if streamStatus === 'error' && streamError}
		<div class="text-xs text-red-700 dark:text-red-300">{streamError}</div>
	{/if}
</div>

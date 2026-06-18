<script lang="ts">
	import { Collapsible } from 'bits-ui';
	import { onDestroy, onMount } from 'svelte';

	import { createAgentRunEventsSource, getAgentRunEvents } from '$lib/apis/agentRuns';
	import ContentRenderer from '$lib/components/chat/Messages/ContentRenderer.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Check from '$lib/components/icons/Check.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import ClockRotateRight from '$lib/components/icons/ClockRotateRight.svelte';
	import Document from '$lib/components/icons/Document.svelte';
	import Sparkles from '$lib/components/icons/Sparkles.svelte';
	import Users from '$lib/components/icons/Users.svelte';
	import Wrench from '$lib/components/icons/Wrench.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';

	import { createAgentRunEventState } from './eventFold';
	import { createAgentRunEventsStore } from './store';
	import {
		AGENT_RUN_EVENT_TYPES,
		type AgentRunEventCategory,
		type AgentRunEvent,
		type AgentRunEventState,
		type AgentRunEventViewItem
	} from './types';

	export let agentRunId: string;
	export let showFinalText = true;

	const eventsStore = createAgentRunEventsStore();
	let state: AgentRunEventState = createAgentRunEventState();
	let expandedSeqs = new Set<number>();
	let streamStatus: 'loading' | 'live' | 'reconnecting' | 'error' = 'loading';
	let streamError = '';

	const unsubscribe = eventsStore.subscribe((nextState) => {
		state = nextState;
		const nextExpandedSeqs = new Set(expandedSeqs);
		for (const item of nextState.items) {
			if (item.status === 'error') {
				nextExpandedSeqs.add(item.seq);
			}
		}
		expandedSeqs = nextExpandedSeqs;
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

	const runStatusMeta = (status: AgentRunEventState['runStatus']) => {
		switch (status) {
			case 'queued':
				return {
					label: 'Queued',
					description: 'Waiting to start',
					className:
						'border-gray-200 bg-gray-50 text-gray-700 dark:border-gray-800 dark:bg-gray-900/50 dark:text-gray-200'
				};
			case 'running':
				return {
					label: 'Running',
					description: 'Working through the task',
					className:
						'border-amber-300/70 bg-amber-50/80 text-amber-900 dark:border-amber-700/60 dark:bg-amber-950/30 dark:text-amber-100'
				};
			case 'waiting_approval':
				return {
					label: 'Needs approval',
					description: 'Waiting for a user decision',
					className:
						'border-blue-300/70 bg-blue-50/80 text-blue-900 dark:border-blue-700/60 dark:bg-blue-950/30 dark:text-blue-100'
				};
			case 'finalizing':
				return {
					label: 'Finalizing',
					description: 'Writing the final answer',
					className:
						'border-indigo-300/70 bg-indigo-50/80 text-indigo-900 dark:border-indigo-700/60 dark:bg-indigo-950/30 dark:text-indigo-100'
				};
			case 'completed':
				return {
					label: 'Completed',
					description: 'Run finished',
					className:
						'border-green-300/70 bg-green-50/80 text-green-900 dark:border-green-800/60 dark:bg-green-950/30 dark:text-green-100'
				};
			case 'failed':
				return {
					label: 'Failed',
					description: 'Run stopped with an error',
					className:
						'border-red-300/70 bg-red-50/80 text-red-800 dark:border-red-800/60 dark:bg-red-950/30 dark:text-red-100'
				};
			case 'cancelled':
				return {
					label: 'Cancelled',
					description: 'Run was cancelled',
					className:
						'border-gray-300 bg-gray-100 text-gray-700 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200'
				};
			case 'budget_exceeded':
				return {
					label: 'Budget exceeded',
					description: 'Run stopped at its budget limit',
					className:
						'border-red-300/70 bg-red-50/80 text-red-800 dark:border-red-800/60 dark:bg-red-950/30 dark:text-red-100'
				};
		}
	};

	const itemStatusClass = (status: AgentRunEventViewItem['status']) => {
		if (status === 'error') {
			return 'border-red-300/70 bg-red-50/70 text-red-800 dark:border-red-800/60 dark:bg-red-950/30 dark:text-red-200';
		}
		if (status === 'running') {
			return 'border-amber-300/70 bg-amber-50/70 text-amber-900 dark:border-amber-700/60 dark:bg-amber-950/30 dark:text-amber-100';
		}

		return 'border-gray-200 bg-gray-50 text-gray-700 dark:border-gray-800 dark:bg-gray-900/40 dark:text-gray-200';
	};

	const categoryClass = (category: AgentRunEventCategory) => {
		switch (category) {
			case 'tool':
				return 'bg-blue-500/15 text-blue-700 dark:text-blue-200';
			case 'approval':
				return 'bg-amber-500/20 text-amber-800 dark:text-amber-100';
			case 'artifact':
				return 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-200';
			case 'subagent':
				return 'bg-violet-500/15 text-violet-700 dark:text-violet-200';
			case 'model':
				return 'bg-cyan-500/15 text-cyan-700 dark:text-cyan-200';
			case 'final':
				return 'bg-indigo-500/15 text-indigo-700 dark:text-indigo-200';
			case 'run':
				return 'bg-gray-500/15 text-gray-700 dark:text-gray-200';
			case 'action':
			default:
				return 'bg-gray-500/15 text-gray-700 dark:text-gray-200';
		}
	};

	const countLabels: Array<{ category: AgentRunEventCategory; label: string }> = [
		{ category: 'tool', label: 'Tools' },
		{ category: 'approval', label: 'Approvals' },
		{ category: 'artifact', label: 'Artifacts' },
		{ category: 'subagent', label: 'Subagents' },
		{ category: 'model', label: 'Model' },
		{ category: 'final', label: 'Final' }
	];

	const hasExpandableContent = (item: AgentRunEventViewItem) =>
		item.details !== null || item.metadata.length > 0;

	const isItemOpen = (seq: number) => expandedSeqs.has(seq);

	const setItemOpen = (seq: number, open: boolean) => {
		const nextExpandedSeqs = new Set(expandedSeqs);
		if (open) {
			nextExpandedSeqs.add(seq);
		} else {
			nextExpandedSeqs.delete(seq);
		}
		expandedSeqs = nextExpandedSeqs;
	};

	const streamLabel = (status: typeof streamStatus) => {
		switch (status) {
			case 'loading':
				return 'Loading events';
			case 'live':
				return 'Live';
			case 'reconnecting':
				return 'Reconnecting';
			case 'error':
				return 'Stream error';
		}
	};

	const streamClass = (status: typeof streamStatus) => {
		if (status === 'error') {
			return 'text-red-700 dark:text-red-300';
		}
		if (status === 'reconnecting') {
			return 'text-amber-700 dark:text-amber-300';
		}
		if (status === 'live') {
			return 'text-green-700 dark:text-green-300';
		}
		return 'text-gray-500 dark:text-gray-400';
	};

	$: statusMeta = runStatusMeta(state.runStatus);
</script>

<div
	class="agent-run-events my-2 flex w-full flex-col overflow-hidden rounded-lg border border-gray-200 bg-white text-sm shadow-sm dark:border-gray-800 dark:bg-gray-950/30"
>
	<div class="flex flex-col gap-2 border-b border-gray-100 px-3 py-2.5 dark:border-gray-800/80">
		<div class="flex flex-wrap items-center justify-between gap-2">
			<div class="flex min-w-0 items-center gap-2">
				<div
					class="flex size-7 shrink-0 items-center justify-center rounded-full bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-200"
				>
					{#if state.runStatus === 'completed'}
						<Check className="size-4" strokeWidth="2.5" />
					{:else if state.runStatus === 'failed' || state.runStatus === 'budget_exceeded' || state.runStatus === 'cancelled'}
						<XMark className="size-4" />
					{:else if state.runStatus === 'finalizing'}
						<Sparkles className="size-4" />
					{:else}
						<Spinner className="size-4" />
					{/if}
				</div>

				<div class="min-w-0">
					<div class="flex items-center gap-2">
						<div class="font-medium text-gray-900 dark:text-gray-100">Agent Run</div>
						<Tooltip content={statusMeta.description}>
							<div
								class="rounded-full border px-2 py-0.5 text-[11px] font-medium {statusMeta.className}"
							>
								{statusMeta.label}
							</div>
						</Tooltip>
					</div>
					<div class="mt-0.5 flex items-center gap-1.5 text-xs {streamClass(streamStatus)}">
						{#if streamStatus === 'loading'}
							<Spinner className="size-3" />
						{:else if streamStatus === 'reconnecting'}
							<ClockRotateRight className="size-3.5" />
						{/if}
						<span>{streamLabel(streamStatus)}</span>
					</div>
				</div>
			</div>

			{#if state.items.length > 0}
				<div class="text-xs text-gray-500 dark:text-gray-400">
					{state.items.length}
					{state.items.length === 1 ? 'event' : 'events'}
				</div>
			{/if}
		</div>

		{#if state.items.length > 0}
			<div class="flex flex-wrap gap-1.5">
				{#each countLabels as count}
					{#if state.counts[count.category] > 0}
						<div
							class="rounded-md px-2 py-1 text-[11px] font-medium {categoryClass(count.category)}"
						>
							{count.label}: {state.counts[count.category]}
						</div>
					{/if}
				{/each}
			</div>
		{/if}
	</div>

	{#if state.items.length > 0}
		<div class="flex flex-col" aria-label="Agent run events">
			{#each state.items as item (item.seq)}
				<Collapsible.Root
					class="border-b border-gray-100 last:border-b-0 dark:border-gray-800/80"
					disabled={!hasExpandableContent(item)}
					open={isItemOpen(item.seq)}
					onOpenChange={(open) => setItemOpen(item.seq, open)}
				>
					<Collapsible.Trigger
						class="group flex w-full items-start gap-3 px-3 py-2.5 text-left transition hover:bg-gray-50 disabled:cursor-default disabled:hover:bg-transparent dark:hover:bg-gray-900/40 dark:disabled:hover:bg-transparent"
					>
						<div class="mt-0.5 flex shrink-0 flex-col items-center">
							<div
								class="flex size-6 items-center justify-center rounded-full border {itemStatusClass(
									item.status
								)}"
							>
								{#if item.status === 'running'}
									<Spinner className="size-3.5" />
								{:else if item.status === 'error'}
									<XMark className="size-3.5" />
								{:else if item.category === 'tool'}
									<Wrench className="size-3.5" />
								{:else if item.category === 'artifact'}
									<Document className="size-3.5" />
								{:else if item.category === 'subagent'}
									<Users className="size-3.5" />
								{:else}
									<Check className="size-3.5" strokeWidth="2.5" />
								{/if}
							</div>
						</div>

						<div class="min-w-0 flex-1">
							<div class="flex min-w-0 flex-wrap items-center gap-1.5">
								<span
									class="rounded-md px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-normal {categoryClass(
										item.category
									)}"
								>
									{item.label}
								</span>
								<span class="min-w-0 truncate font-medium text-gray-900 dark:text-gray-100">
									{item.summary}
								</span>
							</div>

							{#if item.metadata.length > 0 || item.participantId}
								<div
									class="mt-1 flex min-w-0 flex-wrap gap-x-2 gap-y-0.5 text-xs text-gray-500 dark:text-gray-400"
								>
									{#each item.metadata as metadata}
										<span class="min-w-0 truncate">
											<span class="text-gray-400 dark:text-gray-500">{metadata.label}</span>
											<span>{metadata.value}</span>
										</span>
									{/each}
									{#if item.participantId}
										<Tooltip content={item.participantId}>
											<span class="max-w-40 truncate">
												<span class="text-gray-400 dark:text-gray-500">Actor</span>
												<span>{item.participantId}</span>
											</span>
										</Tooltip>
									{/if}
								</div>
							{/if}
						</div>

						{#if hasExpandableContent(item)}
							<div
								class="mt-1 shrink-0 text-gray-400 transition group-data-[state=open]:rotate-180 dark:text-gray-500"
							>
								<ChevronDown className="size-3.5" strokeWidth="2.5" />
							</div>
						{/if}
					</Collapsible.Trigger>

					{#if hasExpandableContent(item)}
						<Collapsible.Content class="px-3 pb-3 pl-12 text-xs text-gray-700 dark:text-gray-200">
							{#if item.details}
								<pre
									class="max-h-64 overflow-auto whitespace-pre-wrap rounded-md border border-gray-200 bg-gray-50/80 p-2 leading-relaxed text-gray-800 dark:border-gray-800 dark:bg-black/20 dark:text-gray-100">{detailsText(
										item.details
									)}</pre>
							{/if}
						</Collapsible.Content>
					{/if}
				</Collapsible.Root>
			{/each}
		</div>
	{:else}
		<div class="px-3 py-3 text-xs text-gray-500 dark:text-gray-400">
			Waiting for Agent Run events.
		</div>
	{/if}

	{#if showFinalText && state.finalText}
		<div
			class="agent-run-final-answer border-t border-gray-100 bg-gray-50/70 px-3 py-3 dark:border-gray-800/80 dark:bg-gray-900/30"
		>
			<div
				class="mb-2 flex items-center gap-2 text-xs font-medium text-gray-500 dark:text-gray-400"
			>
				<Sparkles className="size-3.5" />
				<span>Final answer</span>
			</div>
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

	{#if streamStatus === 'error' && streamError}
		<div
			class="border-t border-red-100 px-3 py-2 text-xs text-red-700 dark:border-red-950 dark:text-red-300"
		>
			{streamError}
		</div>
	{/if}
</div>

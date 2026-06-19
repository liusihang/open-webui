<script lang="ts">
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Check from '$lib/components/icons/Check.svelte';
	import ClockRotateRight from '$lib/components/icons/ClockRotateRight.svelte';
	import Sparkles from '$lib/components/icons/Sparkles.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';

	import type { AgentRunRenderModel } from './renderModel';
	import type { AgentRunEventCategory, AgentRunState } from './types';

	export let model: AgentRunRenderModel;
	export let streamError = '';

	const runStatusMeta = (status: AgentRunState) => {
		switch (status) {
			case 'queued':
				return {
					label: 'Waiting',
					description: 'Waiting to start',
					className:
						'border-gray-200 bg-gray-50 text-gray-700 dark:border-gray-800 dark:bg-gray-900/50 dark:text-gray-200'
				};
			case 'running':
				return {
					label: 'Working',
					description: 'Working through the task',
					className:
						'border-amber-300/70 bg-amber-50/80 text-amber-900 dark:border-amber-700/60 dark:bg-amber-950/30 dark:text-amber-100'
				};
			case 'waiting_approval':
				return {
					label: 'Needs review',
					description: 'Waiting for a user decision',
					className:
						'border-blue-300/70 bg-blue-50/80 text-blue-900 dark:border-blue-700/60 dark:bg-blue-950/30 dark:text-blue-100'
				};
			case 'finalizing':
				return {
					label: 'Answering',
					description: 'Writing the final answer',
					className:
						'border-indigo-300/70 bg-indigo-50/80 text-indigo-900 dark:border-indigo-700/60 dark:bg-indigo-950/30 dark:text-indigo-100'
				};
			case 'completed':
				return {
					label: 'Completed',
					description: 'Task finished',
					className:
						'border-green-300/70 bg-green-50/80 text-green-900 dark:border-green-800/60 dark:bg-green-950/30 dark:text-green-100'
				};
			case 'failed':
				return {
					label: 'Failed',
					description: 'Task stopped with an error',
					className:
						'border-red-300/70 bg-red-50/80 text-red-800 dark:border-red-800/60 dark:bg-red-950/30 dark:text-red-100'
				};
			case 'cancelled':
				return {
					label: 'Cancelled',
					description: 'Task was cancelled',
					className:
						'border-gray-300 bg-gray-100 text-gray-700 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200'
				};
			case 'budget_exceeded':
				return {
					label: 'Limit reached',
					description: 'Task stopped at its budget limit',
					className:
						'border-red-300/70 bg-red-50/80 text-red-800 dark:border-red-800/60 dark:bg-red-950/30 dark:text-red-100'
				};
		}
	};

	const streamLabel = (status: AgentRunRenderModel['transportStatus']) => {
		switch (status) {
			case 'loading':
				return 'Loading updates';
			case 'live':
				return 'Updating now';
			case 'reconnecting':
				return 'Connection retrying';
			case 'error':
				return 'Connection issue';
			case 'closed':
				return 'Updates complete';
		}
	};

	const streamClass = (status: AgentRunRenderModel['transportStatus']) => {
		if (status === 'error') {
			return 'text-red-700 dark:text-red-300';
		}
		if (status === 'reconnecting') {
			return 'text-amber-700 dark:text-amber-300';
		}
		if (status === 'live') {
			return 'text-green-700 dark:text-green-300';
		}
		if (status === 'closed') {
			return 'text-gray-500 dark:text-gray-400';
		}
		return 'text-gray-500 dark:text-gray-400';
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
			case 'action':
			default:
				return 'bg-gray-500/15 text-gray-700 dark:text-gray-200';
		}
	};

	const countLabels: Array<{ category: AgentRunEventCategory; label: string }> = [
		{ category: 'tool', label: 'Actions' },
		{ category: 'approval', label: 'Reviews' },
		{ category: 'artifact', label: 'Files' },
		{ category: 'subagent', label: 'Helpers' },
		{ category: 'model', label: 'Setup' },
		{ category: 'final', label: 'Answer' }
	];

	$: statusMeta = runStatusMeta(model.runStatus);
	$: eventCount = model.groups.reduce((total, group) => total + group.events.length, 0);
</script>

<div class="flex flex-col gap-2 border-b border-gray-100 px-3 py-2.5 dark:border-gray-800/80">
	<div class="flex flex-wrap items-center justify-between gap-2">
		<div class="flex min-w-0 items-center gap-2">
			<div
				class="flex size-7 shrink-0 items-center justify-center rounded-full bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-200"
			>
				{#if model.runStatus === 'completed'}
					<Check className="size-4" strokeWidth="2.5" />
				{:else if model.runStatus === 'failed' || model.runStatus === 'budget_exceeded' || model.runStatus === 'cancelled'}
					<XMark className="size-4" />
				{:else if model.runStatus === 'finalizing'}
					<Sparkles className="size-4" />
				{:else}
					<Spinner className="size-4" />
				{/if}
			</div>

			<div class="min-w-0">
				<div class="flex items-center gap-2">
					<div class="font-medium text-gray-900 dark:text-gray-100">Assistant task</div>
					<Tooltip content={statusMeta.description}>
						<div
							class="rounded-full border px-2 py-0.5 text-[11px] font-medium {statusMeta.className}"
						>
							{statusMeta.label}
						</div>
					</Tooltip>
				</div>
				<div class="mt-0.5 flex items-center gap-1.5 text-xs {streamClass(model.transportStatus)}">
					{#if model.transportStatus === 'loading'}
						<Spinner className="size-3" />
					{:else if model.transportStatus === 'reconnecting'}
						<ClockRotateRight className="size-3.5" />
					{/if}
					<span>{streamLabel(model.transportStatus)}</span>
				</div>
			</div>
		</div>

		{#if eventCount > 0}
			<div class="text-xs text-gray-500 dark:text-gray-400">
				{eventCount}
				{eventCount === 1 ? 'update' : 'updates'}
			</div>
		{/if}
	</div>

	{#if eventCount > 0}
		<div class="flex flex-wrap gap-1.5">
			{#each countLabels as count}
				{#if model.counts[count.category] > 0}
					<div class="rounded-md px-2 py-1 text-[11px] font-medium {categoryClass(count.category)}">
						{count.label}: {model.counts[count.category]}
					</div>
				{/if}
			{/each}
		</div>
	{/if}

	{#if model.transportStatus === 'error' && streamError}
		<div
			class="rounded-md border border-red-100 bg-red-50/70 px-2.5 py-2 text-xs text-red-700 dark:border-red-950 dark:bg-red-950/20 dark:text-red-300"
		>
			{streamError}
		</div>
	{/if}
</div>

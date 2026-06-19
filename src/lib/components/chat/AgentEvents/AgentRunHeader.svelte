<script lang="ts">
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Check from '$lib/components/icons/Check.svelte';
	import Sparkles from '$lib/components/icons/Sparkles.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';

	import type { AgentRunRenderModel } from './renderModel';
	import type { AgentRunState } from './types';

	export let model: AgentRunRenderModel;

	const runStatusMeta = (status: AgentRunState) => {
		switch (status) {
			case 'queued':
				return {
					label: '等待中',
					description: '正在等待开始',
					className:
						'border-gray-200 bg-gray-50 text-gray-700 dark:border-gray-800 dark:bg-gray-900/50 dark:text-gray-200'
				};
			case 'running':
				return {
					label: '处理中',
					description: '正在处理任务',
					className:
						'border-amber-300/70 bg-amber-50/80 text-amber-900 dark:border-amber-700/60 dark:bg-amber-950/30 dark:text-amber-100'
				};
			case 'waiting_approval':
				return {
					label: '需要确认',
					description: '等待用户确认',
					className:
						'border-blue-300/70 bg-blue-50/80 text-blue-900 dark:border-blue-700/60 dark:bg-blue-950/30 dark:text-blue-100'
				};
			case 'finalizing':
				return {
					label: '正在回答',
					description: '正在生成最终回答',
					className:
						'border-indigo-300/70 bg-indigo-50/80 text-indigo-900 dark:border-indigo-700/60 dark:bg-indigo-950/30 dark:text-indigo-100'
				};
			case 'completed':
				return {
					label: '已完成',
					description: '任务已完成',
					className:
						'border-green-300/70 bg-green-50/80 text-green-900 dark:border-green-800/60 dark:bg-green-950/30 dark:text-green-100'
				};
			case 'failed':
				return {
					label: '失败',
					description: '任务因错误停止',
					className:
						'border-red-300/70 bg-red-50/80 text-red-800 dark:border-red-800/60 dark:bg-red-950/30 dark:text-red-100'
				};
			case 'cancelled':
				return {
					label: '已取消',
					description: '任务已取消',
					className:
						'border-gray-300 bg-gray-100 text-gray-700 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200'
				};
			case 'budget_exceeded':
				return {
					label: '达到限制',
					description: '任务已达到预算限制',
					className:
						'border-red-300/70 bg-red-50/80 text-red-800 dark:border-red-800/60 dark:bg-red-950/30 dark:text-red-100'
				};
		}
	};

	const streamMessage = (status: AgentRunRenderModel['transportStatus']) => {
		switch (status) {
			case 'loading':
				return '正在准备任务进展';
			case 'reconnecting':
				return '正在恢复任务进展';
			case 'error':
				return '实时进展已暂停';
			case 'live':
			case 'closed':
			default:
				return null;
		}
	};

	const streamClass = (status: AgentRunRenderModel['transportStatus']) => {
		if (status === 'error') {
			return 'text-red-700 dark:text-red-300';
		}
		if (status === 'reconnecting') {
			return 'text-amber-700 dark:text-amber-300';
		}

		return 'text-gray-500 dark:text-gray-400';
	};

	$: statusMeta = runStatusMeta(model.runStatus);
	$: statusLine = streamMessage(model.transportStatus);
</script>

<div class="flex flex-col gap-2 border-b border-gray-100 px-3 py-2.5 dark:border-gray-800/80">
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
				<div class="font-medium text-gray-900 dark:text-gray-100">任务进展</div>
				<Tooltip content={statusMeta.description}>
					<div
						class="rounded-full border px-2 py-0.5 text-[11px] font-medium {statusMeta.className}"
					>
						{statusMeta.label}
					</div>
				</Tooltip>
			</div>
			{#if statusLine}
				<div class="mt-0.5 text-xs {streamClass(model.transportStatus)}">{statusLine}</div>
			{/if}
		</div>
	</div>
</div>

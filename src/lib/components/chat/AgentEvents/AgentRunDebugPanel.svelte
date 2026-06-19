<script lang="ts">
	import AgentRunTimeline from './AgentRunTimeline.svelte';
	import type { AgentRunRenderModel } from './renderModel';

	export let agentRunId: string;
	export let model: AgentRunRenderModel;
	export let expandedGroupIds: Set<string> = new Set();
	export let setGroupOpen: (id: string, open: boolean) => void = () => {};
	export let streamError = '';

	const transportLabel = (status: AgentRunRenderModel['transportStatus']) => {
		switch (status) {
			case 'loading':
				return 'loading';
			case 'live':
				return 'live';
			case 'reconnecting':
				return 'reconnecting';
			case 'error':
				return 'error';
			case 'closed':
				return 'closed';
		}
	};
</script>

{#if model.debugGroups.length > 0 || model.transportStatus === 'error' || model.transportStatus === 'reconnecting'}
	<details class="border-t border-gray-100 px-3 py-2.5 dark:border-gray-800/80">
		<summary
			class="cursor-pointer text-xs font-medium text-gray-500 marker:text-gray-400 dark:text-gray-400 dark:marker:text-gray-500"
		>
			调试详情
		</summary>

		<div class="mt-3 space-y-3 text-xs text-gray-600 dark:text-gray-300">
			<div class="grid gap-2 sm:grid-cols-2">
				<div>
					<span class="text-gray-400 dark:text-gray-500">Run ID</span>
					<div class="mt-0.5 break-all font-mono text-[11px]">{agentRunId}</div>
				</div>
				<div>
					<span class="text-gray-400 dark:text-gray-500">Transport</span>
					<div class="mt-0.5 font-mono text-[11px]">{transportLabel(model.transportStatus)}</div>
				</div>
				<div>
					<span class="text-gray-400 dark:text-gray-500">最后事件序号</span>
					<div class="mt-0.5 font-mono text-[11px]">{model.debug.lastSeq}</div>
				</div>
				<div>
					<span class="text-gray-400 dark:text-gray-500">已折叠事件</span>
					<div class="mt-0.5 font-mono text-[11px]">{model.debug.hiddenEvents}</div>
				</div>
			</div>

			{#if streamError}
				<div
					class="rounded-md border border-red-100 bg-red-50/70 px-2.5 py-2 text-red-700 dark:border-red-950 dark:bg-red-950/20 dark:text-red-300"
				>
					{streamError}
				</div>
			{/if}

			{#if model.debugGroups.length > 0}
				<div class="overflow-hidden rounded-md border border-gray-200 dark:border-gray-800">
					<AgentRunTimeline
						groups={model.debugGroups}
						artifacts={[]}
						{expandedGroupIds}
						{setGroupOpen}
						label="调试事件"
						emptyText="暂无调试事件。"
					/>
				</div>
			{/if}
		</div>
	</details>
{/if}

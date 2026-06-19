<script lang="ts">
	import AgentArtifactCard from './AgentArtifactCard.svelte';
	import AgentRunEventItem from './AgentRunEventItem.svelte';
	import type { AgentRunArtifactPart, AgentRunRenderGroup } from './renderModel';

	export let groups: AgentRunRenderGroup[] = [];
	export let artifacts: AgentRunArtifactPart[] = [];
	export let expandedGroupIds: Set<string> = new Set();
	export let setGroupOpen: (id: string, open: boolean) => void = () => {};
	export let label = '任务进展';
	export let emptyText = '正在准备任务进展。';

	const isOpen = (id: string) => expandedGroupIds.has(id);
</script>

{#if groups.length > 0}
	<div class="flex flex-col" aria-label={label}>
		{#if artifacts.length > 0}
			<div class="border-b border-gray-100 px-3 py-2.5 dark:border-gray-800/80">
				<div class="mb-1.5 text-[11px] font-medium text-gray-500 dark:text-gray-400">文件</div>
				<div class="grid gap-2 sm:grid-cols-2">
					{#each artifacts as artifact (artifact.id)}
						<AgentArtifactCard {artifact} />
					{/each}
				</div>
			</div>
		{/if}

		{#each groups as group (group.id)}
			<AgentRunEventItem
				{group}
				open={isOpen(group.id)}
				onOpenChange={(open) => setGroupOpen(group.id, open)}
			/>
		{/each}
	</div>
{:else}
	<div class="px-3 py-3 text-xs text-gray-500 dark:text-gray-400">{emptyText}</div>
{/if}

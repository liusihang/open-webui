<script lang="ts">
	import AgentArtifactCard from './AgentArtifactCard.svelte';
	import AgentRunEventItem from './AgentRunEventItem.svelte';
	import type { AgentRunArtifactPart, AgentRunRenderGroup } from './renderModel';

	export let groups: AgentRunRenderGroup[] = [];
	export let artifacts: AgentRunArtifactPart[] = [];
	export let expandedGroupIds: Set<string> = new Set();
	export let setGroupOpen: (id: string, open: boolean) => void = () => {};

	const isOpen = (id: string) => expandedGroupIds.has(id);
</script>

{#if groups.length > 0}
	<div class="flex flex-col" aria-label="Agent run events">
		{#if artifacts.length > 0}
			<div class="border-b border-gray-100 px-3 py-2.5 dark:border-gray-800/80">
				<div class="mb-1.5 text-[11px] font-medium text-gray-500 dark:text-gray-400">Artifacts</div>
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
	<div class="px-3 py-3 text-xs text-gray-500 dark:text-gray-400">
		Waiting for Agent Run events.
	</div>
{/if}

<script lang="ts">
	import AgentDetailSection from './AgentDetailSection.svelte';
	import type { AgentRunRenderGroup } from './renderModel';

	export let group: AgentRunRenderGroup;
</script>

<div class="space-y-3">
	{#if group.subtitle || group.metadata.length > 0}
		<div class="space-y-1 text-xs text-gray-500 dark:text-gray-400">
			{#if group.subtitle}
				<div>{group.subtitle}</div>
			{/if}
			{#if group.metadata.length > 0}
				<div class="flex flex-wrap gap-x-2 gap-y-0.5">
					{#each group.metadata as metadata}
						<span>
							<span class="text-gray-400 dark:text-gray-500">{metadata.label}</span>
							<span>{metadata.value}</span>
						</span>
					{/each}
				</div>
			{/if}
		</div>
	{/if}

	{#each group.detailSections as section (section.id)}
		<AgentDetailSection {section} />
	{/each}
</div>

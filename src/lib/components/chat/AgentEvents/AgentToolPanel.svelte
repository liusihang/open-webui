<script lang="ts">
	import AgentDetailSection from './AgentDetailSection.svelte';
	import type { AgentRunRenderGroup } from './renderModel';

	export let group: AgentRunRenderGroup;

	$: visibleSections = group.detailSections.filter((section) => section.kind !== 'debug');
	$: debugSections = group.detailSections.filter((section) => section.kind === 'debug');
</script>

<div class="space-y-3">
	{#if group.subtitle}
		<div class="text-xs text-gray-500 dark:text-gray-400">{group.subtitle}</div>
	{/if}

	{#each visibleSections as section (section.id)}
		<AgentDetailSection {section} />
	{/each}

	{#each debugSections as section (section.id)}
		<AgentDetailSection {section} />
	{/each}
</div>

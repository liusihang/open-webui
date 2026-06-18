<script lang="ts">
	import AgentDetailSection from './AgentDetailSection.svelte';
	import type { AgentRunRenderGroup } from './renderModel';

	export let group: AgentRunRenderGroup;

	$: tone =
		group.status === 'waiting'
			? 'border-amber-200 bg-amber-50/70 text-amber-900 dark:border-amber-800/60 dark:bg-amber-950/20 dark:text-amber-100'
			: 'border-gray-200 bg-gray-50/80 text-gray-700 dark:border-gray-800 dark:bg-gray-900/40 dark:text-gray-200';
	$: label = group.status === 'waiting' ? 'Waiting for your review' : 'Review recorded';
</script>

<div class="space-y-3">
	<div class="rounded-md border px-2.5 py-2 text-xs {tone}">
		<div class="font-medium">{label}</div>
		{#if group.subtitle}
			<div class="mt-0.5 text-[11px] opacity-80">{group.subtitle}</div>
		{/if}
	</div>

	{#each group.detailSections as section (section.id)}
		<AgentDetailSection {section} />
	{/each}
</div>

<script lang="ts">
	import Document from '$lib/components/icons/Document.svelte';

	import type { AgentRunArtifactPart } from './renderModel';

	export let artifact: AgentRunArtifactPart;
</script>

<div
	class="flex min-w-0 items-start gap-2 rounded-md border border-gray-200 bg-gray-50/70 px-2.5 py-2 text-xs dark:border-gray-800 dark:bg-gray-900/40"
>
	<div
		class="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full bg-emerald-500/15 text-emerald-700 dark:text-emerald-200"
	>
		<Document className="size-3.5" />
	</div>
	<div class="min-w-0 flex-1">
		<div class="truncate font-medium text-gray-900 dark:text-gray-100">{artifact.name}</div>
		{#if artifact.path}
			<div class="mt-0.5 truncate text-gray-500 dark:text-gray-400">{artifact.path}</div>
		{/if}
		<div class="mt-1 flex flex-wrap gap-x-2 gap-y-0.5 text-[11px] text-gray-500 dark:text-gray-400">
			{#if artifact.mimeType}
				<span>{artifact.mimeType}</span>
			{/if}
			{#if artifact.size}
				<span>{artifact.size} bytes</span>
			{/if}
		</div>
	</div>
</div>

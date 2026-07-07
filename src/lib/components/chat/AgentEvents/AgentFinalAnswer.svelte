<script lang="ts">
	import ContentRenderer from '$lib/components/chat/Messages/ContentRenderer.svelte';
	import Sparkles from '$lib/components/icons/Sparkles.svelte';

	export let agentRunId: string;
	export let content = '';
	export let done = false;
	export let quiet = false;
	export let history: any;
	export let messageId: string;
</script>

{#if quiet}
	<ContentRenderer
		id={`agent-run-final-${agentRunId}`}
		{content}
		{done}
		{history}
		{messageId}
		floatingButtons={false}
		save={false}
		preview={false}
	/>
{:else}
	<div
		class="agent-run-final-answer border-t border-gray-100 bg-gray-50/70 px-3 py-3 dark:border-gray-800/80 dark:bg-gray-900/30"
	>
		<div class="mb-2 flex items-center gap-2 text-xs font-medium text-gray-500 dark:text-gray-400">
			<Sparkles className="size-3.5" />
			<span>最终回答</span>
		</div>
		<ContentRenderer
			id={`agent-run-final-${agentRunId}`}
			{content}
			{done}
			{history}
			{messageId}
			floatingButtons={false}
			save={false}
			preview={false}
		/>
	</div>
{/if}

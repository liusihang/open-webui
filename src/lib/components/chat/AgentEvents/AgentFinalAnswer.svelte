<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import ContentRenderer from '$lib/components/chat/Messages/ContentRenderer.svelte';
	import Sparkles from '$lib/components/icons/Sparkles.svelte';

	export let agentRunId: string;
	export let content = '';
	export let done = false;
	export let quiet = false;
	export let history: any;
	export let messageId: string;

	const i18n = getContext<Writable<i18nType>>('i18n');
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
			<span>{$i18n.t('Final answer')}</span>
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

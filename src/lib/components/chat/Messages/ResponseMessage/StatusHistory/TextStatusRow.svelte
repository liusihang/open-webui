<script lang="ts">
	import ContentRenderer from '../../ContentRenderer.svelte';

	type TextDetail = {
		text?: {
			blockId: string;
			content: string;
			participantId?: string | null;
		};
	};

	export let detail: TextDetail = {};
	export let done = false;

	$: text = detail?.text?.content ?? '';
	$: blockId = detail?.text?.blockId ?? 'segment';
</script>

{#if text}
	<div class="w-full text-xs">
		{#if done}
			<ContentRenderer
				id={`agent-text-${blockId}`}
				content={text}
				done={true}
				floatingButtons={false}
			/>
		{:else}
			<pre
				class="m-0 whitespace-pre-wrap break-words font-sans text-xs text-gray-900 dark:text-gray-100">{text}<span
					class="agent-text-cursor">|</span
				></pre>
		{/if}
	</div>
{/if}

<style>
	.agent-text-cursor {
		animation: blink 1s steps(2, start) infinite;
		opacity: 0.6;
	}

	@keyframes blink {
		to {
			visibility: hidden;
		}
	}
</style>

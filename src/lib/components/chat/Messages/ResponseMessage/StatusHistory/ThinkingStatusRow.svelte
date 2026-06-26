<script lang="ts">
	import { getContext } from 'svelte';

	import Spinner from '$lib/components/common/Spinner.svelte';

	const i18n = getContext('i18n');

	export let done = false;
	export let description = '思考中...';
</script>

<div class="flex items-center gap-2 py-0.5 w-full text-left">
	{#if done}
		<span class="flex-shrink-0 mt-0.5 text-gray-400 dark:text-gray-500">
			<svg
				class="w-3.5 h-3.5"
				viewBox="0 0 24 24"
				fill="none"
				stroke="currentColor"
				stroke-width="2.5"
			>
				<path d="M5 13l4 4L19 7" stroke-linecap="round" stroke-linejoin="round" />
			</svg>
		</span>
	{:else}
		<span class="flex-shrink-0 mt-0.5 text-gray-400 dark:text-gray-500">
			<Spinner className="w-3.5 h-3.5" />
		</span>
	{/if}
	<span
		class="{done ? '' : 'shimmer'} text-gray-500 dark:text-gray-500 text-[13px] line-clamp-1 text-wrap"
	>
		{done ? $i18n.t('Thinking complete') : $i18n.t(description)}
	</span>
</div>

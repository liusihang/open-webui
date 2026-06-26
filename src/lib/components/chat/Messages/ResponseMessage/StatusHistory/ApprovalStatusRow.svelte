<script lang="ts">
	import { getContext } from 'svelte';

	const i18n = getContext('i18n');

	export let description = '';
	export let done = false;
	export let detail: { input?: unknown } | undefined;

	$: inputText = (() => {
		const value = detail?.input;
		if (value === undefined || value === null) return '';
		if (typeof value === 'string') return value;
		try {
			return JSON.stringify(value);
		} catch {
			return '';
		}
	})();
</script>

<div
	class="flex items-start gap-2 w-full text-left {done
		? 'py-0.5 text-gray-500 dark:text-gray-400'
		: 'rounded-lg border border-amber-200/80 bg-amber-50/70 px-2 py-1 text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/20 dark:text-amber-100'}"
>
	<span
		class="flex-shrink-0 mt-0.5 {done
			? 'text-gray-400 dark:text-gray-500'
			: 'text-amber-500 dark:text-amber-400'}"
	>
		{#if done}
			<svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
				<path d="M5 13l4 4L19 7" stroke-linecap="round" stroke-linejoin="round" />
			</svg>
		{:else}
			<svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
				<circle cx="12" cy="12" r="9" stroke-dasharray="4 3" />
			</svg>
		{/if}
	</span>

	<div class="min-w-0 flex-1">
		<div
			class="{done ? 'text-gray-600 dark:text-gray-300' : 'text-amber-900 dark:text-amber-100'} text-[13px] line-clamp-1 text-wrap"
		>
			{done ? $i18n.t('Approval confirmed') : $i18n.t('Approval needed')}：{description}
		</div>
		{#if inputText}
			<div class="mt-0.5 text-xs text-gray-500 dark:text-gray-400 line-clamp-2 text-wrap">
				{inputText}
			</div>
		{/if}
	</div>
</div>

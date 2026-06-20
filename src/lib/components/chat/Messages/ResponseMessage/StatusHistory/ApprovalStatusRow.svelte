<script lang="ts">
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

<div class="flex items-start gap-2 py-0.5 w-full text-left">
	<span class="flex-shrink-0 mt-0.5 text-amber-500 dark:text-amber-400">
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
		<div class="text-amber-700 dark:text-amber-300 text-base line-clamp-1 text-wrap">
			{done ? '已确认' : '需要确认'}：{description}
		</div>
		{#if inputText}
			<div class="mt-0.5 text-xs text-gray-500 dark:text-gray-400 line-clamp-2 text-wrap">
				{inputText}
			</div>
		{/if}
	</div>
</div>

<script lang="ts">
	import { slide } from 'svelte/transition';

	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';

	type ErrorDetail = { message?: string; code?: string } | string;

	export let description = '';
	export let detail: { error?: ErrorDetail } | undefined;

	let open = false;

	const errorText = (value: ErrorDetail | undefined): string => {
		if (typeof value === 'string') return value;
		if (!value) return '';
		const parts = [value.message, value.code ? `[${value.code}]` : null].filter(
			Boolean
		);
		return parts.join(' ');
	};

	$: errorMessage = detail?.error ? errorText(detail.error) : '';
</script>

<div class="flex items-start gap-2 py-0.5 w-full text-left">
	<span class="flex-shrink-0 mt-0.5 text-red-500 dark:text-red-400">
		<svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
			<path d="M12 9v3.75m0 3.75v.008M4.5 12a7.5 7.5 0 1015 0 7.5 7.5 0 00-15 0z" stroke-linecap="round" />
		</svg>
	</span>

	<div class="min-w-0 flex-1">
		<button
			type="button"
			class="flex items-center gap-1 min-w-0 w-full text-left"
			on:click={() => (open = !open)}
		>
			<span class="text-red-700 dark:text-red-300 text-base line-clamp-1 text-wrap">
				{errorMessage || description}
			</span>
			<ChevronDown
				className="size-3 shrink-0 text-red-400 transition-transform {open ? 'rotate-180' : ''}"
				strokeWidth="2.5"
			/>
		</button>

		{#if open && errorMessage}
			<div class="mt-1.5" transition:slide={{ duration: 150 }}>
				<pre
					class="max-h-40 overflow-auto whitespace-pre-wrap rounded-md border border-red-200 bg-red-50/70 p-2 text-xs text-red-700 dark:border-red-900/60 dark:bg-red-950/20 dark:text-red-200"
				>{description}{#if errorMessage !== description ? `\n\n${errorMessage}` : ''}{/if}</pre>
			</div>
		{/if}
	</div>
</div>

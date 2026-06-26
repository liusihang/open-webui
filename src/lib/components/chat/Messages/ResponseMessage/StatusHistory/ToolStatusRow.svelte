<script lang="ts">
	import { getContext } from 'svelte';
	import { slide } from 'svelte/transition';

	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';

	const i18n = getContext('i18n');

	type ToolDetail = {
		input?: unknown;
		output?: unknown;
		error?: { message?: string; code?: string } | string;
	};

	export let description = '';
	export let done = false;
	export let detail: ToolDetail | undefined;

	let open = false;

	const formatValue = (value: unknown) =>
		typeof value === 'string' ? value : JSON.stringify(value, null, 2);

	$: hasDetail =
		detail !== undefined &&
		(detail.input !== undefined ||
			detail.output !== undefined ||
			detail.error !== undefined);
</script>

{#if hasDetail}
	<details bind:open={open} class="w-full">
		<summary class="flex list-none items-start gap-2 py-0.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gray-300/70 dark:focus-visible:ring-gray-700/70">
			<span class="flex-shrink-0 mt-0.5 text-gray-400 dark:text-gray-500">
				{#if done}
					<svg
						class="w-3.5 h-3.5"
						viewBox="0 0 24 24"
						fill="none"
						stroke="currentColor"
						stroke-width="2.5"
					>
						<path d="M5 13l4 4L19 7" stroke-linecap="round" stroke-linejoin="round" />
					</svg>
				{:else}
					<svg
						class="w-3.5 h-3.5 animate-spin"
						viewBox="0 0 24 24"
						fill="none"
						stroke="currentColor"
						stroke-width="2.5"
					>
						<path d="M12 3a9 9 0 1 0 9 9" stroke-linecap="round" />
					</svg>
				{/if}
			</span>

			<div class="min-w-0 flex-1">
				<span
					class="{done ? '' : 'shimmer'} text-gray-600 dark:text-gray-300 text-[13px] line-clamp-1 text-wrap"
				>
					{description}
				</span>
			</div>

			<span class="shrink-0 text-[11px] text-gray-400 dark:text-gray-500">
				{$i18n.t('Debug details')}
			</span>
			<ChevronDown
				className="size-3 shrink-0 text-gray-400 transition-transform {open ? 'rotate-180' : ''}"
				strokeWidth="2.5"
			/>
		</summary>

		{#if open && detail}
			<div class="mt-1.5 space-y-1.5 pl-5" transition:slide={{ duration: 150 }}>
				{#if detail.input !== undefined}
					<div>
						<div class="text-[11px] font-medium text-gray-500 dark:text-gray-400">
							{$i18n.t('Request')}
						</div>
						<pre
							class="mt-0.5 max-h-40 overflow-auto whitespace-pre-wrap rounded-md border border-gray-200 bg-gray-50/70 p-2 text-xs text-gray-700 dark:border-gray-800 dark:bg-gray-900/40 dark:text-gray-200"
						>{formatValue(detail.input)}</pre>
					</div>
				{/if}
				{#if detail.output !== undefined}
					<div>
						<div class="text-[11px] font-medium text-gray-500 dark:text-gray-400">
							{$i18n.t('Result')}
						</div>
						<pre
							class="mt-0.5 max-h-40 overflow-auto whitespace-pre-wrap rounded-md border border-gray-200 bg-gray-50/70 p-2 text-xs text-gray-700 dark:border-gray-800 dark:bg-gray-900/40 dark:text-gray-200"
						>{formatValue(detail.output)}</pre>
					</div>
				{/if}
			</div>
		{/if}
	</details>
{:else}
	<div class="flex items-start gap-2 py-0.5 w-full text-left">
		<span class="flex-shrink-0 mt-0.5 text-gray-400 dark:text-gray-500">
			{#if done}
				<svg
					class="w-3.5 h-3.5"
					viewBox="0 0 24 24"
					fill="none"
					stroke="currentColor"
					stroke-width="2.5"
				>
					<path d="M5 13l4 4L19 7" stroke-linecap="round" stroke-linejoin="round" />
				</svg>
			{:else}
				<svg
					class="w-3.5 h-3.5 animate-spin"
					viewBox="0 0 24 24"
					fill="none"
					stroke="currentColor"
					stroke-width="2.5"
				>
					<path d="M12 3a9 9 0 1 0 9 9" stroke-linecap="round" />
				</svg>
			{/if}
		</span>

		<div class="min-w-0 flex-1">
			<span
				class="{done ? '' : 'shimmer'} text-gray-600 dark:text-gray-300 text-[13px] line-clamp-1 text-wrap"
			>
				{description}
			</span>
		</div>
	</div>
{/if}

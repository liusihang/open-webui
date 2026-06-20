<script lang="ts">
	import { slide } from 'svelte/transition';

	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import Users from '$lib/components/icons/Users.svelte';

	type SubagentDetail = { name?: string; resultSummary?: string };

	export let description = '';
	export let done = false;
	export let detail: { subagent?: SubagentDetail; output?: unknown } | undefined;

	let open = false;

	const formatValue = (value: unknown) =>
		typeof value === 'string' ? value : JSON.stringify(value, null, 2);

	$: name = detail?.subagent?.name ?? description ?? '助手';
	$: summary = detail?.subagent?.resultSummary ?? '';
</script>

<div class="flex items-start gap-2 py-0.5 w-full text-left">
	<span class="flex-shrink-0 mt-0.5 text-violet-500 dark:text-violet-400">
		<Users className="w-3.5 h-3.5" />
	</span>

	<div class="min-w-0 flex-1">
		<button
			type="button"
			class="flex items-center gap-1 min-w-0 w-full text-left"
			on:click={() => (open = !open)}
		>
			<span class="text-gray-700 dark:text-gray-300 text-base line-clamp-1 text-wrap">
				{name}
			</span>
			{#if !done}
				<svg
					class="w-3 h-3 shrink-0 animate-spin text-gray-400"
					viewBox="0 0 24 24"
					fill="none"
					stroke="currentColor"
					stroke-width="2.5"
				>
					<path d="M12 3a9 9 0 1 0 9 9" stroke-linecap="round" />
				</svg>
			{/if}
			<ChevronDown
				className="size-3 shrink-0 text-gray-400 transition-transform {open ? 'rotate-180' : ''}"
				strokeWidth="2.5"
			/>
		</button>

		{#if open}
			<div class="mt-1.5 space-y-1" transition:slide={{ duration: 150 }}>
				{#if summary}
					<div class="text-xs text-gray-600 dark:text-gray-300">{summary}</div>
				{/if}
				{#if detail?.output !== undefined}
					<pre
						class="max-h-40 overflow-auto whitespace-pre-wrap rounded-md border border-gray-200 bg-gray-50/70 p-2 text-xs text-gray-700 dark:border-gray-800 dark:bg-gray-900/40 dark:text-gray-200"
					>{formatValue(detail.output)}</pre>
				{/if}
			</div>
		{/if}
	</div>
</div>

<script>
	import { getContext } from 'svelte';
	const i18n = getContext('i18n');

	import BookOpen from '$lib/components/icons/BookOpen.svelte';
	import CheckCircle from '$lib/components/icons/CheckCircle.svelte';
	import ClockRotateRight from '$lib/components/icons/ClockRotateRight.svelte';
	import Wrench from '$lib/components/icons/Wrench.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import Search from '$lib/components/icons/Search.svelte';
	import WebSearchResults from '../WebSearchResults.svelte';

	export let status = null;
	export let done = false;

	$: isRunning = (done || status?.done) === false;
	$: isFailed =
		status?.error === true ||
		status?.action === 'error' ||
		status?.action === 'failed' ||
		status?.status === 'failed';

	$: actionIcon =
		status?.action === 'web_search' ||
		status?.action === 'web_search_queries_generated' ||
		status?.action === 'queries_generated'
			? Search
			: status?.action === 'knowledge_search'
				? BookOpen
				: Wrench;

	$: stateIcon = isFailed ? XMark : isRunning ? ClockRotateRight : CheckCircle;
	$: stateIconClass = isFailed
		? 'text-red-500'
		: isRunning
			? 'text-violet-500 animate-spin'
			: 'text-emerald-500';
</script>

{#if !status?.hidden}
	<div class="status-description flex items-start gap-2 py-0.5 w-full text-left">
		<div class="mt-0.5 rounded-md bg-gray-100 p-1 text-gray-500 dark:bg-gray-850 dark:text-gray-300">
			<svelte:component this={actionIcon} className="size-3.5" />
		</div>

		<div class="flex-1 min-w-0">
			{#if status?.action === 'web_search' && (status?.urls || status?.items)}
				<WebSearchResults {status}>
					<div class="flex flex-col justify-center -space-y-0.5">
						<div class="{isRunning ? 'shimmer' : ''} text-sm line-clamp-1 text-wrap">
							{#if status?.description?.includes('{{count}}')}
								{$i18n.t(status?.description, {
									count: (status?.urls || status?.items).length
								})}
							{:else if status?.description === 'No search query generated'}
								{$i18n.t('No search query generated')}
							{:else if status?.description === 'Generating search query'}
								{$i18n.t('Generating search query')}
							{:else}
								{status?.description}
							{/if}
						</div>
					</div>
				</WebSearchResults>
			{:else if status?.action === 'knowledge_search'}
				<div class="flex flex-col justify-center -space-y-0.5">
					<div class="{isRunning ? 'shimmer' : ''} text-gray-500 dark:text-gray-400 text-sm line-clamp-1 text-wrap">
						{$i18n.t(`Searching Knowledge for "{{searchQuery}}"`, {
							searchQuery: status.query
						})}
					</div>
				</div>
			{:else if status?.action === 'web_search_queries_generated' && status?.queries}
				<div class="flex flex-col justify-center -space-y-0.5">
					<div class="{isRunning ? 'shimmer' : ''} text-gray-500 dark:text-gray-400 text-sm line-clamp-1 text-wrap">
						{$i18n.t(`Searching`)}
					</div>

					<div class="flex gap-1 flex-wrap mt-2">
						{#each status.queries as query, idx (query)}
							<div class="bg-gray-50 dark:bg-gray-850 flex rounded-lg py-1 px-2 items-center gap-1 text-xs">
								<div>
									<Search className="size-3" />
								</div>

								<span class="line-clamp-1">
									{query}
								</span>
							</div>
						{/each}
					</div>
				</div>
			{:else if status?.action === 'queries_generated' && status?.queries}
				<div class="flex flex-col justify-center -space-y-0.5">
					<div class="{isRunning ? 'shimmer' : ''} text-gray-500 dark:text-gray-400 text-sm line-clamp-1 text-wrap">
						{$i18n.t(`Querying`)}
					</div>

					<div class="flex gap-1 flex-wrap mt-2">
						{#each status.queries as query, idx (query)}
							<div class="bg-gray-50 dark:bg-gray-850 flex rounded-lg py-1 px-2 items-center gap-1 text-xs">
								<div>
									<Search className="size-3" />
								</div>

								<span class="line-clamp-1">
									{query}
								</span>
							</div>
						{/each}
					</div>
				</div>
			{:else if status?.action === 'sources_retrieved' && status?.count !== undefined}
				<div class="flex flex-col justify-center -space-y-0.5">
					<div class="{isRunning ? 'shimmer' : ''} text-gray-500 dark:text-gray-400 text-sm line-clamp-1 text-wrap">
						{#if status.count === 0}
							{$i18n.t('No sources found')}
						{:else if status.count === 1}
							{$i18n.t('Retrieved 1 source')}
						{:else}
							{$i18n.t('Retrieved {{count}} sources', {
								count: status.count
							})}
						{/if}
					</div>
				</div>
			{:else}
				<div class="flex flex-col justify-center -space-y-0.5">
					<div class="{isRunning ? 'shimmer' : ''} text-gray-500 dark:text-gray-400 text-sm line-clamp-1 text-wrap">
						{#if status?.description?.includes('{{searchQuery}}')}
							{$i18n.t(status?.description, {
								searchQuery: status?.query
							})}
						{:else if status?.description === 'No search query generated'}
							{$i18n.t('No search query generated')}
						{:else if status?.description === 'Generating search query'}
							{$i18n.t('Generating search query')}
						{:else if status?.description === 'Searching the web'}
							{$i18n.t('Searching the web')}
						{:else}
							{status?.description}
						{/if}
					</div>
				</div>
			{/if}
		</div>

		<div class="mt-1 shrink-0">
			<svelte:component this={stateIcon} className={`size-3.5 ${stateIconClass}`} />
		</div>
	</div>
{/if}

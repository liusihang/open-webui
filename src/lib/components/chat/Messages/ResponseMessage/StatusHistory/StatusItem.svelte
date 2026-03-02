<script>
	import { getContext } from 'svelte';
	const i18n = getContext('i18n');

	import BookOpen from '$lib/components/icons/BookOpen.svelte';
	import CheckCircle from '$lib/components/icons/CheckCircle.svelte';
	import ClockRotateRight from '$lib/components/icons/ClockRotateRight.svelte';
	import Wrench from '$lib/components/icons/Wrench.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import Search from '$lib/components/icons/Search.svelte';
	import Markdown from '$lib/components/chat/Messages/Markdown.svelte';
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
			: status?.action === 'knowledge_search' ||
				  status?.action === 'deep_research' ||
				  status?.action === 'memory_retrieval' ||
				  status?.action === 'memory_writeback'
				? BookOpen
				: Wrench;

	$: stateIcon = isFailed ? XMark : isRunning ? ClockRotateRight : CheckCircle;
	$: stateIconClass = isFailed
		? 'text-red-500'
		: isRunning
			? 'text-violet-500 animate-spin'
			: 'text-emerald-500';
	$: webSearchCount = status?.urls?.length ?? status?.items?.length ?? 0;

	$: normalizedStepChildren = (() => {
		if (Array.isArray(status?.children)) {
			return status.children
				.map((child, idx) => {
					if (!child) {
						return {
							title: `Step ${idx + 1}`,
							markdown: ''
						};
					}

					if (typeof child === 'string') {
						return {
							title: `Step ${idx + 1}`,
							markdown: child
						};
					}

					return {
						title: child?.title || `Step ${idx + 1}`,
						markdown: typeof child?.markdown === 'string' ? child.markdown : ''
					};
				})
				.filter((child) => child.title || child.markdown);
		}

		if (typeof status?.markdown === 'string' && status.markdown.trim()) {
			return [
				{
					title: 'Step details',
					markdown: status.markdown
				}
			];
		}

		return [];
	})();
</script>

{#if !status?.hidden}
	<div class="status-description flex items-start gap-2 py-0.5 w-full text-left">
		<div
			class="mt-0.5 rounded-md bg-gray-100 p-1 text-gray-500 dark:bg-gray-850 dark:text-gray-300"
		>
			<svelte:component this={actionIcon} className="size-3.5" />
		</div>

		<div class="flex-1 min-w-0">
			{#if status?.action === 'web_search' && (status?.urls || status?.items)}
				<WebSearchResults {status}>
					<div class="flex flex-col justify-center -space-y-0.5">
						<div class="{isRunning ? 'shimmer' : ''} text-sm line-clamp-1 text-wrap">
							{#if status?.description?.includes('{{count}}')}
								{$i18n.t(status?.description, {
									count: webSearchCount
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
			{:else if status?.action === 'deep_research'}
				<div class="flex flex-col justify-center gap-2">
					<div
						class="{isRunning ? 'shimmer' : ''} text-gray-500 dark:text-gray-400 text-sm text-wrap"
					>
						{status?.description}
					</div>

					{#if normalizedStepChildren.length > 0}
						<div class="space-y-2">
							{#each normalizedStepChildren as child, idx}
								<div
									class="rounded-lg border border-gray-200/70 bg-gray-50/70 px-2.5 py-2 dark:border-gray-800/70 dark:bg-gray-900/35"
								>
									<div class="text-sm font-medium text-gray-700 dark:text-gray-200">
										{child.title || `Step ${idx + 1}`}
									</div>

									{#if child.markdown}
										<div
											class="status-step-markdown mt-1 text-sm leading-6 text-gray-600 dark:text-gray-300"
										>
											<Markdown content={child.markdown} done={true} editCodeBlock={false} />
										</div>
									{/if}
								</div>
							{/each}
						</div>
					{/if}
				</div>
			{:else if status?.action === 'knowledge_search'}
				<div class="flex flex-col justify-center -space-y-0.5">
					<div
						class="{isRunning
							? 'shimmer'
							: ''} text-gray-500 dark:text-gray-400 text-sm line-clamp-1 text-wrap"
					>
						{$i18n.t(`Searching Knowledge for "{{searchQuery}}"`, {
							searchQuery: status.query
						})}
					</div>
				</div>
			{:else if status?.action === 'web_search_queries_generated' && status?.queries}
				<div class="flex flex-col justify-center -space-y-0.5">
					<div
						class="{isRunning
							? 'shimmer'
							: ''} text-gray-500 dark:text-gray-400 text-sm line-clamp-1 text-wrap"
					>
						{$i18n.t(`Searching`)}
					</div>

					<div class="flex gap-1 flex-wrap mt-2">
						{#each status.queries as query (query)}
							<div
								class="bg-gray-50 dark:bg-gray-850 flex rounded-lg py-1 px-2 items-center gap-1 text-xs"
							>
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
					<div
						class="{isRunning
							? 'shimmer'
							: ''} text-gray-500 dark:text-gray-400 text-sm line-clamp-1 text-wrap"
					>
						{$i18n.t(`Querying`)}
					</div>

					<div class="flex gap-1 flex-wrap mt-2">
						{#each status.queries as query (query)}
							<div
								class="bg-gray-50 dark:bg-gray-850 flex rounded-lg py-1 px-2 items-center gap-1 text-xs"
							>
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
					<div
						class="{isRunning
							? 'shimmer'
							: ''} text-gray-500 dark:text-gray-400 text-sm line-clamp-1 text-wrap"
					>
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
			{:else if status?.action === 'memory_retrieval'}
				<div class="flex flex-col justify-center -space-y-0.5">
					<div
						class="{isRunning
							? 'shimmer'
							: ''} text-gray-500 dark:text-gray-400 text-sm line-clamp-1 text-wrap"
					>
						{#if isRunning}
							{$i18n.t('Searching memories')}
						{:else if status?.error === true}
							{$i18n.t('Failed to retrieve memories')}
						{:else if status?.count === 0}
							{$i18n.t('No matching memories found')}
						{:else if (status?.injected_count ?? 0) > 0}
							{$i18n.t('Retrieved {{count}} memories, injected {{injectedCount}}', {
								count: status?.count ?? 0,
								injectedCount: status?.injected_count ?? 0
							})}
						{:else}
							{$i18n.t('Retrieved {{count}} memories, no context injected', {
								count: status?.count ?? 0
							})}
						{/if}
					</div>
				</div>
			{:else if status?.action === 'memory_writeback'}
				<div class="flex flex-col justify-center -space-y-0.5">
					<div
						class="{isRunning
							? 'shimmer'
							: ''} text-gray-500 dark:text-gray-400 text-sm line-clamp-1 text-wrap"
					>
						{#if isRunning}
							{$i18n.t('Updating memories')}
						{:else if status?.error === true}
							{$i18n.t('Memory writeback failed')}
						{:else if (status?.applied_count ?? 0) === 0}
							{$i18n.t('No memory changes applied')}
						{:else}
							{$i18n.t('Saved {{addedCount}}, updated {{updatedCount}}, deleted {{deletedCount}}', {
								addedCount: status?.added_count ?? 0,
								updatedCount: status?.updated_count ?? 0,
								deletedCount: status?.deleted_count ?? 0
							})}
						{/if}
					</div>
				</div>
			{:else}
				<div class="flex flex-col justify-center -space-y-0.5">
					<div
						class="{isRunning
							? 'shimmer'
							: ''} text-gray-500 dark:text-gray-400 text-sm line-clamp-1 text-wrap"
					>
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

<style>
	:global(.status-step-markdown p) {
		margin: 0;
	}

	:global(.status-step-markdown p + p) {
		margin-top: 0.45rem;
	}

	:global(.status-step-markdown ul),
	:global(.status-step-markdown ol) {
		margin-top: 0.45rem;
		margin-bottom: 0;
		padding-left: 1.2rem;
	}
</style>

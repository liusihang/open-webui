<script lang="ts">
	import { getContext } from 'svelte';
	import { embed, showControls, showEmbeds } from '$lib/stores';

	import CitationModal from './Citations/CitationModal.svelte';
	import {
		buildCitationTargets,
		calculateShowRelevance,
		groupCitationTargetsForDisplay,
		shouldShowPercentage
	} from './citations';

	const i18n = getContext('i18n');

	export let id = '';
	export let chatId = '';

	export let sources = [];
	export let content = '';
	export let metadata = null;
	export let readOnly = false;

	let citationTargets = [];
	let sourceGroups = [];
	let citations = [];
	let showPercentage = false;
	let showRelevance = true;

	let showCitations = false;
	let showCitationModal = false;

	let selectedCitation: any = null;

	export const showSourceModal = (sourceId) => {
		let index;
		let suffix = null;

		if (typeof sourceId === 'string') {
			const output = sourceId.split('#');
			index = parseInt(output[0]) - 1;

			if (output.length > 1) {
				suffix = output[1];
			}
		} else {
			index = sourceId - 1;
		}

		const target =
			typeof sourceId === 'string' || typeof sourceId === 'number'
				? citationTargets.find((item) => item.number === index + 1)
				: null;
		const citation = target?.citation ?? citations[index];

		if (citation) {
			console.log('Showing citation modal for:', citation);

			if (citation?.source?.embed_url) {
				const embedUrl = citation.source.embed_url;
				if (embedUrl) {
					if (readOnly) {
						// Open in new tab if readOnly
						window.open(embedUrl, '_blank');
						return;
					} else {
						showControls.set(true);
						showEmbeds.set(true);
						embed.set({
							url: embedUrl,
							title: citation?.source?.name || 'Embedded Content',
							source: citation,
							chatId: chatId,
							messageId: id,
							sourceId: sourceId
						});
					}
				} else {
					selectedCitation = { ...citation, preview: target?.preview };
					showCitationModal = true;
				}
			} else {
				selectedCitation = { ...citation, preview: target?.preview };
				showCitationModal = true;
			}
		}
	};

	$: {
		citationTargets = buildCitationTargets(sources ?? [], { content, metadata });
		sourceGroups = groupCitationTargetsForDisplay(citationTargets);
		citations = citationTargets.map((target) => target.citation);
		showRelevance = calculateShowRelevance(citations);
		showPercentage = shouldShowPercentage(citations);
	}

	const decodeString = (str: string) => {
		try {
			return decodeURIComponent(str);
		} catch (e) {
			return str;
		}
	};

	const getDomain = (url: string) => {
		const domain = url.replace('http://', '').replace('https://', '').split(/[/?#]/)[0];
		return domain.startsWith('www.') ? domain.slice(4) : domain;
	};

	const getTargetHost = (target: any) => {
		const url =
			typeof target?.citation?.source?.url === 'string'
				? target.citation.source.url
				: typeof target?.citation?.source?.name === 'string' &&
					  target.citation.source.name.startsWith('http')
					? target.citation.source.name
					: '';
		return url ? getDomain(url) : '';
	};

	const getTargetKind = (target: any) => {
		const sourceName = `${target?.citation?.source?.name ?? ''}`.toLowerCase();
		const sourceUrl = `${target?.citation?.source?.url ?? ''}`.toLowerCase();
		const metadata = target?.citation?.metadata ?? [];
		if (
			sourceName.endsWith('.pdf') ||
			sourceUrl.endsWith('.pdf') ||
			metadata.some((item: any) => item?.file_id)
		) {
			return 'PDF';
		}
		if (sourceUrl.startsWith('http') || sourceName.startsWith('http')) {
			return 'Web';
		}
		if (metadata.some((item: any) => item?.evidence_ref)) {
			return 'Evidence';
		}
		return 'Source';
	};

	const getTargetPages = (target: any): number[] => {
		const pages = new Set<number>();
		for (const metadata of target?.citation?.metadata ?? []) {
			if (Number.isInteger(metadata?.page)) {
				pages.add(metadata.page + 1);
			}
			if (Number.isInteger(metadata?.page_index)) {
				pages.add(metadata.page_index + 1);
			}
		}
		if (Number.isInteger(target?.preview?.page_index)) {
			pages.add(target.preview.page_index + 1);
		}
		return Array.from(pages).sort((a, b) => a - b);
	};

	const getTargetSnippet = (target: any) => {
		const previewText =
			target?.preview?.text || target?.preview?.caption || target?.preview?.ocr_text || '';
		const documentText =
			typeof target?.citation?.document?.[0] === 'string' ? target.citation.document[0] : '';
		const compact = `${previewText || documentText}`.replace(/\s+/g, ' ').trim();
		if (compact.length <= 180) {
			return compact;
		}
		return `${compact.slice(0, 180).trimEnd()}…`;
	};
</script>

<CitationModal
	bind:show={showCitationModal}
	citation={selectedCitation}
	{showPercentage}
	{showRelevance}
/>

{#if citations.length > 0}
	{@const urlCitations = citations.filter((c) => c?.source?.name?.startsWith('http'))}
	<div class="w-full py-1">
		<button
			class="inline-flex h-8 items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 text-xs font-semibold text-gray-700 shadow-sm transition hover:border-blue-200 hover:text-blue-700 dark:border-gray-800 dark:bg-gray-900 dark:text-gray-200 dark:hover:border-blue-500/30 dark:hover:text-blue-200"
			aria-label={citations.length === 1
				? $i18n.t('Toggle 1 source')
				: $i18n.t('Toggle {{COUNT}} sources', { COUNT: citations.length })}
			aria-expanded={showCitations}
			on:click={() => {
				showCitations = !showCitations;
			}}
		>
			{#if urlCitations.length > 0}
				<div class="flex -space-x-1 items-center">
					{#each urlCitations.slice(0, 3) as citation, idx}
						<img
							src="https://www.google.com/s2/favicons?sz=32&domain={citation.source.name}"
							alt="favicon"
							class="size-4 rounded-full shrink-0 border border-white dark:border-gray-850 bg-white dark:bg-gray-900"
							on:error={(e) => {
								(e.currentTarget as HTMLImageElement).src = '/favicon.png';
							}}
						/>
					{/each}
					{#if citations.length > 3}
						<div
							class="flex size-4 shrink-0 items-center justify-center rounded-full border border-white bg-blue-100 text-[8px] font-semibold tracking-tighter text-blue-700 dark:border-gray-850 dark:bg-blue-950 dark:text-blue-200"
							aria-hidden="true"
						>
							+{citations.length - Math.min(urlCitations.length, 3)}
						</div>
					{/if}
				</div>
			{/if}
			<span>{$i18n.t('Sources')}</span>
			<span
				class="rounded-md border border-blue-200 bg-blue-50 px-1.5 py-0.5 text-[11px] leading-none text-blue-700 dark:border-blue-500/30 dark:bg-blue-500/10 dark:text-blue-200"
			>
				{citations.length === 1 ? $i18n.t('1 Source') : citations.length}
			</span>
			<span class="text-gray-400 dark:text-gray-500" aria-hidden="true">
				{showCitations ? '-' : '+'}
			</span>
		</button>
	</div>
{/if}

{#if showCitations}
	<div
		class="my-2 rounded-lg border border-gray-200 bg-white shadow-sm dark:border-gray-800 dark:bg-gray-900"
	>
		<div
			class="flex items-center justify-between gap-3 border-b border-gray-100 px-3 py-2 dark:border-gray-800"
		>
			<div class="text-sm font-semibold text-gray-900 dark:text-gray-100">
				{$i18n.t('Sources')}
			</div>
			<div
				class="rounded-md border border-blue-200 bg-blue-50 px-2 py-0.5 text-xs font-semibold text-blue-700 dark:border-blue-500/30 dark:bg-blue-500/10 dark:text-blue-200"
			>
				{citations.length === 1
					? $i18n.t('1 Source')
					: $i18n.t('{{COUNT}} Sources', { COUNT: citations.length })}
			</div>
		</div>
		<div class="divide-y divide-gray-100 dark:divide-gray-800">
			{#each sourceGroups as group, groupIdx}
				{@const groupTarget = group.targets[0]}
				{@const host = getTargetHost(groupTarget)}
				<div class="p-3">
					<div class="flex min-w-0 items-start gap-3">
						<div
							class="inline-flex size-8 shrink-0 items-center justify-center rounded-md bg-blue-50 text-xs font-semibold text-blue-700 dark:bg-blue-500/10 dark:text-blue-200"
						>
							{String.fromCharCode(65 + (groupIdx % 26))}
						</div>
						<div class="min-w-0 flex-1">
							<div class="line-clamp-1 font-semibold text-gray-900 dark:text-gray-100">
								{decodeString(group.title)}
							</div>
							<div
								class="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-gray-500 dark:text-gray-400"
							>
								<span
									class="rounded bg-gray-100 px-1.5 py-0.5 font-medium text-gray-600 dark:bg-gray-850 dark:text-gray-300"
								>
									{getTargetKind(groupTarget)}
								</span>
								{#if host}
									<span class="truncate">{host}</span>
								{/if}
								<span>
									{group.targets.length === 1
										? $i18n.t('1 Source')
										: $i18n.t('{{COUNT}} references', { COUNT: group.targets.length })}
								</span>
							</div>
						</div>
					</div>
					<div class="mt-2 grid gap-2 sm:grid-cols-2">
						{#each group.targets as target}
							{@const pages = getTargetPages(target)}
							{@const snippet = getTargetSnippet(target)}
							<button
								id={`source-${id}-${target.number}`}
								aria-label={$i18n.t('View source: {{name}}', {
									name: decodeString(target.title)
								})}
								class="no-toggle group/source flex min-w-0 items-start gap-2 rounded-md border border-gray-100 bg-gray-50/60 p-2.5 text-left outline-hidden transition hover:border-blue-200 hover:bg-blue-50/50 focus:border-blue-300 focus:ring-2 focus:ring-blue-500/20 dark:border-gray-800 dark:bg-gray-950/40 dark:hover:border-blue-500/30 dark:hover:bg-blue-500/10"
								on:click={() => {
									showCitationModal = true;
									selectedCitation = { ...target.citation, preview: target.preview };
								}}
							>
								<div
									class="inline-flex size-6 shrink-0 items-center justify-center rounded-md bg-white text-[11px] font-semibold tabular-nums text-blue-700 shadow-xs dark:bg-gray-900 dark:text-blue-200"
								>
									{target.number}
								</div>
								<div class="min-w-0 flex-1 space-y-1">
									<div class="line-clamp-1 text-sm font-semibold text-gray-900 dark:text-gray-100">
										{pages.length > 0
											? `${$i18n.t('Page')} ${pages[0]}`
											: decodeString(target.title)}
									</div>
									{#if snippet}
										<div class="line-clamp-2 text-xs leading-5 text-gray-600 dark:text-gray-300">
											{snippet}
										</div>
									{/if}
									{#if pages.length > 1}
										<div class="flex flex-wrap gap-1 pt-0.5">
											{#each pages.slice(1, 4) as page}
												<span
													class="rounded-md border border-gray-200 bg-white px-1.5 py-0.5 text-[11px] font-medium text-gray-600 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300"
												>
													{$i18n.t('Page')}
													{page}
												</span>
											{/each}
											{#if pages.length > 4}
												<span
													class="rounded-md border border-gray-200 bg-white px-1.5 py-0.5 text-[11px] font-medium text-gray-500 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-400"
												>
													+{pages.length - 4}
												</span>
											{/if}
										</div>
									{/if}
								</div>
							</button>
						{/each}
					</div>
				</div>
			{/each}
		</div>
	</div>
{/if}

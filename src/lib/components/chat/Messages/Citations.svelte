<script lang="ts">
	import { getContext } from 'svelte';
	import { embed, showControls, showEmbeds } from '$lib/stores';
	import {
		buildCitations,
		calculateShowRelevance,
		shouldShowPercentage,
		summarizeCitations
	} from './citations';

	import CitationModal from './Citations/CitationModal.svelte';

	const i18n = getContext('i18n');

	export let id = '';
	export let chatId = '';

	export let sources = [];
	export let readOnly = false;

	let citations = [];
	let citationSummary = { items: [], count: 0, urlSources: [], distances: [] };
	let showPercentage = false;
	let showRelevance = true;

	let citationModal = null;

	let showCitations = false;
	let showCitationModal = false;

	let selectedCitation: any = null;

	const ensureCitationsLoaded = () => {
		if (citations.length === 0 && citationSummary.count > 0) {
			citations = buildCitations(sources);
		}
	};

	export const showSourceModal = (sourceId) => {
		ensureCitationsLoaded();

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

		if (citations[index]) {
			console.log('Showing citation modal for:', citations[index]);

			if (citations[index]?.source?.embed_url) {
				const embedUrl = citations[index].source.embed_url;
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
							title: citations[index]?.source?.name || 'Embedded Content',
							source: citations[index],
							chatId: chatId,
							messageId: id,
							sourceId: sourceId
						});
					}
				} else {
					selectedCitation = citations[index];
					showCitationModal = true;
				}
			} else {
				selectedCitation = citations[index];
				showCitationModal = true;
			}
		}
	};

	$: {
		citationSummary = summarizeCitations(sources);
		citations = [];
		showRelevance = calculateShowRelevance(citationSummary.distances);
		showPercentage = shouldShowPercentage(citationSummary.distances);
	}

	const decodeString = (str: string) => {
		try {
			return decodeURIComponent(str);
		} catch (e) {
			return str;
		}
	};
</script>

<CitationModal
	bind:show={showCitationModal}
	citation={selectedCitation}
	{showPercentage}
	{showRelevance}
/>

{#if citationSummary.count > 0}
	<div class=" py-1 -mx-0.5 w-full flex gap-1 items-center flex-wrap">
		<button
			class="text-xs font-medium text-gray-600 dark:text-gray-300 px-3.5 h-8 rounded-full hover:bg-gray-100 dark:hover:bg-gray-800 transition flex items-center gap-1 border border-gray-50 dark:border-gray-850/30"
			aria-label={citationSummary.count === 1
				? $i18n.t('Toggle 1 source')
				: $i18n.t('Toggle {{COUNT}} sources', { COUNT: citationSummary.count })}
			aria-expanded={showCitations}
			on:click={() => {
				showCitations = !showCitations;
				if (showCitations) {
					ensureCitationsLoaded();
				}
			}}
		>
			{#if citationSummary.urlSources.length > 0}
				<div class="flex -space-x-1 items-center">
					{#each citationSummary.urlSources.slice(0, 3) as citation, idx}
						<img
							src="https://www.google.com/s2/favicons?sz=32&domain={citation.source.url || citation.source.name}"
							alt="favicon"
							class="size-4 rounded-full shrink-0 border border-white dark:border-gray-850 bg-white dark:bg-gray-900"
							on:error={(e) => {
								e.target.src = '/favicon.png';
							}}
						/>
					{/each}
				</div>
			{/if}
			<div>
				{#if citationSummary.count === 1}
					{$i18n.t('1 Source')}
				{:else}
					{$i18n.t('{{COUNT}} Sources', {
						COUNT: citationSummary.count
					})}
				{/if}
			</div>
		</button>
	</div>
{/if}

{#if showCitations}
	<div class="py-1.5">
		<div class="text-xs gap-2 flex flex-col">
			{#each citations as citation, idx}
				<button
					id={`source-${id}-${idx + 1}`}
					aria-label={$i18n.t('View source: {{name}}', {
						name: decodeString(citation.source.name)
					})}
					class="no-toggle outline-hidden flex dark:text-gray-300 bg-transparent text-gray-600 rounded-xl gap-1.5 items-center"
					on:click={() => {
						showCitationModal = true;
						selectedCitation = citation;
					}}
				>
					<div class=" font-medium bg-gray-50 dark:bg-gray-850 rounded-md px-1">
						{idx + 1}
					</div>
					<div
						class="flex-1 truncate hover:text-black dark:text-white/60 dark:hover:text-white transition text-left"
					>
						{decodeString(citation.source.name)}
					</div>
				</button>
			{/each}
		</div>
	</div>
{/if}

<script lang="ts">
	import { getContext } from 'svelte';
	import { embed, showControls, showEmbeds } from '$lib/stores';

	import CitationModal from './Citations/CitationModal.svelte';
	import { buildCitationTargets, calculateShowRelevance, shouldShowPercentage } from './citations';

	const i18n = getContext('i18n');

	export let id = '';
	export let chatId = '';

	export let sources = [];
	export let content = '';
	export let metadata = null;
	export let readOnly = false;

	let citationTargets = [];
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
</script>

<CitationModal
	bind:show={showCitationModal}
	citation={selectedCitation}
	{showPercentage}
	{showRelevance}
/>

{#if citations.length > 0}
	{@const urlCitations = citations.filter((c) => c?.source?.name?.startsWith('http'))}
	<div class=" py-1 -mx-0.5 w-full flex gap-1 items-center flex-wrap">
		<button
			class="text-xs font-normal text-gray-600 dark:text-gray-300 px-3.5 h-8 rounded-full hover:bg-gray-100 dark:hover:bg-gray-800 transition flex items-center gap-1 border border-gray-50 dark:border-gray-850/30"
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
								e.target.src = '/favicon.png';
							}}
						/>
					{/each}
					{#if citations.length > 3}
						<div
							class="size-4 rounded-full shrink-0 border border-white dark:border-gray-850 bg-gray-100 dark:bg-gray-800 flex items-center justify-center text-[8px] font-normal text-gray-500 dark:text-gray-400 whitespace-nowrap tracking-tighter"
							aria-hidden="true"
						>
							+{citations.length - Math.min(urlCitations.length, 3)}
						</div>
					{/if}
				</div>
			{/if}
			<div>
				{#if citations.length === 1}
					{$i18n.t('1 Source')}
				{:else}
					{$i18n.t('{{COUNT}} Sources', {
						COUNT: citations.length
					})}
				{/if}
			</div>
		</button>
	</div>
{/if}

{#if showCitations}
	<div class="py-1.5">
		<div class="text-xs gap-2 flex flex-col">
			{#each citationTargets as target, idx}
				<button
					id={`source-${id}-${idx + 1}`}
					aria-label={$i18n.t('View source: {{name}}', {
						name: decodeString(target.title)
					})}
					class="no-toggle outline-hidden flex dark:text-gray-300 bg-transparent text-gray-600 rounded-xl gap-1.5 items-center"
					on:click={() => {
						showCitationModal = true;
						selectedCitation = { ...target.citation, preview: target.preview };
					}}
				>
					<div class=" font-medium bg-gray-50 dark:bg-gray-850 rounded-md px-1">
						{target.number}
					</div>
					<div
						class="flex-1 truncate hover:text-black dark:text-white/60 dark:hover:text-white transition text-left"
					>
						{decodeString(target.title)}
					</div>
				</button>
			{/each}
		</div>
	</div>
{/if}

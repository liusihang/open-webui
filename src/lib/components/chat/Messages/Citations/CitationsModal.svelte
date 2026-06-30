<script lang="ts">
	import { getContext, onMount, tick } from 'svelte';

	const i18n = getContext('i18n');

	import Modal from '$lib/components/common/Modal.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import CitationModal from './CitationModal.svelte';

	export let id = '';
	export let show = false;
	export let citations = [];
	export let showPercentage = false;
	export let showRelevance = true;

	let showCitationModal = false;
	let selectedCitation: any = null;

	export const showCitation = (citation) => {
		selectedCitation = citation;
		showCitationModal = true;
	};

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

<Modal size="lg" bind:show>
	<div class="overflow-hidden rounded-lg">
		<div
			class="flex justify-between gap-3 border-b border-gray-100 px-5 py-3 dark:border-gray-800 dark:text-gray-300"
		>
			<div class="self-center text-base font-semibold capitalize">
				{$i18n.t('Citations')}
			</div>
			<button
				class="self-center rounded-md p-1 text-gray-500 transition hover:bg-gray-100 hover:text-gray-900 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-100"
				aria-label={$i18n.t('Close citation modal')}
				on:click={() => {
					show = false;
				}}
			>
				<XMark className={'size-5'} />
			</button>
		</div>

		<div class="flex flex-col md:flex-row w-full px-5 py-5 md:space-x-4">
			<div
				class="grid max-h-[28rem] w-full gap-2 overflow-y-scroll text-left text-sm scrollbar-hidden dark:text-gray-200 sm:grid-cols-2"
			>
				{#each citations as citation, idx}
					<button
						id={`source-${id}-${idx + 1}`}
						class="no-toggle flex min-w-0 items-start gap-3 rounded-lg border border-gray-100 bg-white p-3 outline-hidden transition hover:border-blue-200 hover:bg-blue-50/40 focus:border-blue-300 focus:ring-2 focus:ring-blue-500/20 dark:border-gray-800 dark:bg-gray-900 dark:hover:border-blue-500/30 dark:hover:bg-blue-500/10"
						on:click={() => {
							showCitationModal = true;
							selectedCitation = citation;
						}}
					>
						<div
							class="inline-flex size-7 shrink-0 items-center justify-center rounded-md bg-blue-50 text-xs font-semibold tabular-nums text-blue-700 dark:bg-blue-500/10 dark:text-blue-200"
						>
							{idx + 1}
						</div>
						<div
							class="min-w-0 flex-1 truncate text-left font-semibold text-gray-900 transition dark:text-gray-100"
						>
							{decodeString(citation.source.name)}
						</div>
					</button>
				{/each}
			</div>
		</div>
	</div>
</Modal>

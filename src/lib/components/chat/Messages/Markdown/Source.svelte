<script lang="ts">
	import { getContext } from 'svelte';
	import { decodeString } from '$lib/utils';

	const i18n = getContext('i18n');

	export let id;

	export let title: string = 'N/A';

	export let onClick: Function = () => {};

	const getCitationNumber = (identifier: string | number) => {
		const value =
			typeof identifier === 'string' ? parseInt(identifier.split('#')[0], 10) : Number(identifier);

		return Number.isInteger(value) && value > 0 ? value : null;
	};

	// Helper function to return only the domain from a URL
	function getDomain(url: string): string {
		const domain = url.replace('http://', '').replace('https://', '').split(/[/?#]/)[0];

		if (domain.startsWith('www.')) {
			return domain.slice(4);
		}
		return domain;
	}

	// Helper function to check if text is a URL and return the domain
	function formattedTitle(title: string): string {
		if (title.startsWith('http')) {
			return getDomain(title);
		}

		return title;
	}

	const getBadgeLabel = (identifier: string | number) => {
		const number = getCitationNumber(identifier);
		return number ? `[${number}]` : '[?]';
	};
</script>

{#if id !== undefined && id !== null}
	<button
		aria-label={$i18n.t('View source: {{title}}', { title: formattedTitle(decodeString(title)) })}
		class="text-[10px] w-fit translate-y-[2px] px-2 py-0.5 dark:bg-white/5 dark:text-white/80 dark:hover:text-white bg-gray-50 text-black/80 hover:text-black transition rounded-xl"
		on:click={() => {
			onClick(id);
		}}
	>
		<span class="font-medium tabular-nums whitespace-nowrap">
			{getBadgeLabel(id)}
		</span>
	</button>
{/if}

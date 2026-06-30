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
		class="inline-flex h-5 w-fit translate-y-[2px] items-center rounded-md border border-blue-200 bg-blue-50 px-1.5 text-[11px] font-semibold leading-none text-blue-700 shadow-sm transition hover:border-blue-300 hover:bg-blue-100 focus:outline-hidden focus:ring-2 focus:ring-blue-500/30 dark:border-blue-500/30 dark:bg-blue-500/10 dark:text-blue-200 dark:hover:bg-blue-500/20"
		on:click={() => {
			onClick(id);
		}}
	>
		<span class="tabular-nums whitespace-nowrap">
			{getBadgeLabel(id)}
		</span>
	</button>
{/if}

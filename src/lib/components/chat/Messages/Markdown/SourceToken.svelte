<script lang="ts">
	import { getContext } from 'svelte';
	import { LinkPreview } from 'bits-ui';
	import { decodeString } from '$lib/utils';
	import Source from './Source.svelte';
	import type { CitationPreview, CitationTarget } from '../citations';

	const i18n = getContext('i18n');

	export let id;
	export let token;
	export let sourceIds: string[] = [];
	export let citationTargets: CitationTarget[] = [];
	export let onClick: Function = () => {};

	let openPreview = false;

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

	const getCitationNumber = (identifier: string | number) => {
		const value =
			typeof identifier === 'string' ? parseInt(identifier.split('#')[0], 10) : Number(identifier);
		return Number.isInteger(value) && value > 0 ? value : null;
	};

	const getBadgeLabel = (identifier: string | number) => {
		const number = getCitationNumber(identifier);
		return number ? `[${number}]` : '[?]';
	};

	const getTarget = (identifier: string | number) => {
		const number = getCitationNumber(identifier);
		if (!number) return null;
		return citationTargets?.find((target) => target?.number === number) ?? null;
	};

	const getTitle = (identifier: string | number) => {
		const number = getCitationNumber(identifier);
		const target = getTarget(identifier);
		return target?.title ?? (number ? sourceIds[number - 1] : undefined) ?? 'N/A';
	};

	const getPreviewText = (preview: CitationPreview) => {
		const raw = preview?.text || preview?.caption || preview?.ocr_text || '';
		const compact = raw.replace(/\s+/g, ' ').trim();
		if (compact.length <= 240) {
			return compact;
		}
		return `${compact.slice(0, 240).trimEnd()}…`;
	};
</script>

{#if sourceIds}
	{#if (token?.ids ?? []).length == 1}
		{@const id = token.ids[0]}
		{@const identifier = token.citationIdentifiers ? token.citationIdentifiers[0] : id}
		{@const target = getTarget(identifier)}
		{#if target?.preview}
			<LinkPreview.Root openDelay={300} closeDelay={100} bind:open={openPreview}>
				<LinkPreview.Trigger>
					<Source id={identifier} title={getTitle(identifier)} {onClick} />
				</LinkPreview.Trigger>
				<LinkPreview.Portal>
					<LinkPreview.Content class="z-[999]" align="start" sideOffset={6}>
						<div class="max-w-80 rounded-lg border border-gray-100 bg-white p-3 text-xs text-gray-700 shadow-lg dark:border-gray-800 dark:bg-gray-900 dark:text-gray-200">
							<div class="mb-2 space-y-1">
								<div class="font-medium text-gray-900 dark:text-gray-100 line-clamp-1">
									{formattedTitle(decodeString(target.preview.title || getTitle(identifier)))}
								</div>
								<div class="flex flex-wrap gap-x-2 gap-y-1 text-[11px] text-gray-500 dark:text-gray-400">
									{#if target.preview.source_name}
										<span>{target.preview.source_name}</span>
									{/if}
									<span>{target.preview.type === 'image' ? $i18n.t('Image') : $i18n.t('Text')}</span>
									{#if Number.isInteger(target.preview.page_index)}
										<span>{$i18n.t('page')} {(target.preview.page_index ?? 0) + 1}</span>
									{/if}
								</div>
							</div>
							{#if target.preview.type === 'image' && target.preview.thumbnail_url}
								<img
									src={target.preview.thumbnail_url}
									alt={target.preview.caption || target.preview.source_name || getTitle(identifier)}
									class="mb-2 max-h-40 w-full rounded-md object-contain bg-gray-50 dark:bg-gray-850"
								/>
							{/if}
							{#if getPreviewText(target.preview)}
								<div class="line-clamp-3 whitespace-pre-wrap break-words leading-5">
									{getPreviewText(target.preview)}
								</div>
							{/if}
						</div>
					</LinkPreview.Content>
				</LinkPreview.Portal>
			</LinkPreview.Root>
		{:else}
			<Source id={identifier} title={getTitle(identifier)} {onClick} />
		{/if}
	{:else}
		<LinkPreview.Root openDelay={300} closeDelay={100} bind:open={openPreview}>
			<LinkPreview.Trigger>
				<button
					aria-label={`${formattedTitle(decodeString(getTitle(token.ids[0])))} +${(token?.ids ?? []).length - 1} ${$i18n.t('more sources')}`}
					class="text-[10px] w-fit translate-y-[2px] px-2 py-0.5 dark:bg-white/5 dark:text-white/80 dark:hover:text-white bg-gray-50 text-black/80 hover:text-black transition rounded-xl"
					on:click={() => {
						openPreview = !openPreview;
					}}
				>
					<span class="font-medium tabular-nums whitespace-nowrap">
						{getBadgeLabel(token.citationIdentifiers ? token.citationIdentifiers[0] : token.ids[0])}
						<span class="dark:text-white/50 text-black/50">
							+{(token?.ids ?? []).length - 1}
						</span>
					</span>
				</button>
			</LinkPreview.Trigger>
			<LinkPreview.Portal>
				<LinkPreview.Content class="z-[999]" align="start" sideOffset={6}>
					<div class="bg-gray-50 dark:bg-gray-850 rounded-xl p-1 cursor-pointer">
						{#each token.citationIdentifiers ?? token.ids as identifier}
							{@const id =
								typeof identifier === 'string' ? parseInt(identifier.split('#')[0]) : identifier}
							<div class="">
								<Source id={identifier} title={getTitle(identifier)} {onClick} />
							</div>
						{/each}
					</div>
				</LinkPreview.Content>
			</LinkPreview.Portal>
		</LinkPreview.Root>
	{/if}
{:else}
	<span>{token.raw}</span>
{/if}

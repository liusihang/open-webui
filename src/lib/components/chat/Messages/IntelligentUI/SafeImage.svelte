<script lang="ts">
	import { getContext } from 'svelte';
	import type { Readable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	const i18n = getContext<Readable<I18n>>('i18n');
	import type { ImageProps } from '$lib/utils/intelligent-ui/extended';
	let { image }: { image: ImageProps } = $props();
	let loaded = $state(false);
	let failed = $state(false);
	$effect(() => {
		image.src;
		loaded = false;
		failed = false;
	});
</script>

<figure class="min-w-0 overflow-hidden rounded-xl border border-gray-200 dark:border-gray-700">
	<div class="relative flex min-h-32 items-center justify-center bg-gray-50 dark:bg-gray-900">
		{#if failed}<p class="p-5 text-sm" role="status">{$i18n.t('Image unavailable')}: {image.alt}</p>
		{:else}
			{#if !loaded}<span class="absolute text-sm text-gray-500" role="status"
					>{$i18n.t('Loading image…')}</span
				>{/if}
			<img
				src={image.src}
				alt={image.alt}
				loading="lazy"
				decoding="async"
				referrerpolicy="no-referrer"
				class="relative max-h-96 w-full object-contain"
				onload={() => (loaded = true)}
				onerror={() => {
					failed = true;
					loaded = false;
				}}
			/>
		{/if}
	</div>
	{#if image.caption || image.source}<figcaption class="space-y-1 p-3 text-sm">
			{#if image.caption}<p>{image.caption}</p>{/if}
			{#if image.source}<a
					href={image.source}
					target="_blank"
					rel="noopener noreferrer"
					referrerpolicy="no-referrer"
					class="underline">{$i18n.t('Source')}</a
				>{/if}
		</figcaption>{/if}
</figure>

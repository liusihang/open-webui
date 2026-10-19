<script lang="ts">
	import { getContext } from 'svelte';
	import type { Readable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	const i18n = getContext<Readable<I18n>>('i18n');
	import type { ImageProps } from '$lib/utils/intelligent-ui/extended';
	import { imageFileId } from '$lib/utils/intelligent-ui/extended';
	import { getFileBlobById } from '$lib/apis/files';
	let { image }: { image: ImageProps } = $props();
	let loaded = $state(false);
	let failed = $state(false);
	let fileSrc = $state('');
	let displaySrc = $derived(imageFileId(image.src) ? fileSrc : image.src);
	$effect(() => {
		const fileId = imageFileId(image.src);
		loaded = false;
		failed = false;
		fileSrc = '';
		if (!fileId) return;
		const controller = new AbortController();
		let active = true;
		let objectUrl = '';
		void (async () => {
			try {
				const blob = await getFileBlobById(localStorage.token ?? '', fileId, {
					signal: controller.signal
				});
				if (!active) return;
				if (!/^image\/(png|jpeg|gif|webp|avif|bmp|x-icon|vnd\.microsoft\.icon)$/i.test(blob.type)) {
					throw new Error('Not a supported raster image');
				}
				objectUrl = URL.createObjectURL(blob);
				fileSrc = objectUrl;
			} catch {
				if (active) failed = true;
			}
		})();
		return () => {
			active = false;
			controller.abort();
			if (objectUrl) URL.revokeObjectURL(objectUrl);
		};
	});
</script>

<figure class="min-w-0 overflow-hidden rounded-xl border border-gray-200 dark:border-gray-700">
	<div class="relative flex min-h-32 items-center justify-center bg-gray-50 dark:bg-gray-900">
		{#if failed}<p class="p-5 text-sm" role="status">{$i18n.t('Image unavailable')}: {image.alt}</p>
		{:else}
			{#if !loaded}<span class="absolute text-sm text-gray-500" role="status"
					>{$i18n.t('Loading image…')}</span
				>{/if}
			{#if displaySrc}<img
					src={displaySrc}
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
				/>{/if}
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

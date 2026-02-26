<script lang="ts">
	import DOMPurify from 'dompurify';
	import { marked } from 'marked';
	import { getContext } from 'svelte';

	import { updateUserSettings } from '$lib/apis/users';
	import { settings } from '$lib/stores';

	import Modal from './common/Modal.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';

	const i18n = getContext('i18n');

	export let show = false;
	export let announcementKey = '';
	export let title = '';
	export let content = '';

	$: sanitizedContent = DOMPurify.sanitize(marked.parse(content ?? '') as string);

	const closeModal = async () => {
		const key = (announcementKey ?? '').trim();

		if (key) {
			localStorage.setItem('announcementModalKey', key);

			const nextSettings = { ...$settings, announcementModalKey: key };
			settings.set(nextSettings);

			try {
				await updateUserSettings(localStorage.token, { ui: nextSettings });
			} catch (error) {
				console.error(error);
			}
		}

		show = false;
	};
</script>

<Modal bind:show size="lg">
	<div class="px-6 pt-5 dark:text-white text-black">
		<div class="flex justify-between items-start">
			<div class="text-xl font-medium">
				{title?.trim() || $i18n.t('Announcement')}
			</div>
			<button class="self-center" on:click={closeModal} aria-label={$i18n.t('Close')}>
				<XMark className={'size-5'}>
					<p class="sr-only">{$i18n.t('Close')}</p>
				</XMark>
			</button>
		</div>
	</div>

	<div class="w-full p-4 px-5 text-gray-700 dark:text-gray-100">
		<div class="overflow-y-scroll max-h-[30rem] scrollbar-hidden">
			<div class="mb-3 markdown-prose-sm !list-none !w-full !max-w-none">
				{@html sanitizedContent}
			</div>
		</div>
		<div class="flex justify-end pt-3 text-sm font-medium">
			<button
				on:click={closeModal}
				class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
			>
				<span class="relative">{$i18n.t('Got it')}</span>
			</button>
		</div>
	</div>
</Modal>

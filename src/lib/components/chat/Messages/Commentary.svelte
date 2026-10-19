<script lang="ts">
	import { getContext } from 'svelte';
	import Collapsible from '$lib/components/common/Collapsible.svelte';

	export let collapsed = false;
	export let compactPreview = false;

	const i18n = getContext<typeof import('$lib/i18n').default>('i18n');
	// Reset only when completion/answer availability changes, not on every text delta.
	$: open = !collapsed;
</script>

<div data-commentary class="my-1 text-gray-600 dark:text-gray-400">
	<Collapsible
		title={$i18n.t('Progress updates') as any}
		bind:open
		className="w-full"
		buttonClassName="py-0.5 text-xs text-gray-600 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-300 transition"
	>
		<div
			slot="content"
			class="border-s-2 border-gray-200 dark:border-gray-700 ps-3 py-1 {compactPreview
				? 'text-xs'
				: 'text-sm'}"
		>
			<slot />
		</div>
	</Collapsible>
</div>

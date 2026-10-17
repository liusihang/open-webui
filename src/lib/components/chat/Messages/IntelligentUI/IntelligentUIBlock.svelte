<script lang="ts">
	import { getContext, setContext } from 'svelte';
	import { CatalogRenderer } from '@json-render/svelte';
	import { parseIntelligentUI } from '$lib/utils/intelligent-ui';
	import Element from './Element.svelte';
	import {
		INTELLIGENT_UI_HOST,
		INTELLIGENT_UI_BLOCK,
		type IntelligentUIHost,
		type InteractionState
	} from './context';

	let {
		source,
		done = false,
		blockId
	}: { source: string; done?: boolean; blockId: string } = $props();
	const host = getContext<IntelligentUIHost | undefined>(INTELLIGENT_UI_HOST);
	let local = $state<InteractionState>({});
	let showSource = $state(false);
	const registry = {
		Stack: Element,
		Chart: Element,
		ComparisonCards: Element,
		Explanation: Element,
		ParameterSlider: Element
	};
	let parsed = $derived(parseIntelligentUI(source, done));
	let renderSpec = $derived(
		parsed.spec
			? {
					...parsed.spec,
					elements: Object.fromEntries(
						Object.entries(parsed.spec.elements).map(([key, element]) => [
							key,
							{ ...element, props: { ...element.props, __key: key } }
						])
					)
				}
			: null
	);

	setContext(INTELLIGENT_UI_BLOCK, {
		read: (key: string, fallback: number | boolean) => {
			const value = local[key] ?? host?.read(blockId)?.[key];
			return typeof value === typeof fallback &&
				(typeof value !== 'number' || Number.isFinite(value))
				? value
				: fallback;
		},
		write: (key: string, value: number | boolean) => {
			local = { ...host?.read(blockId), ...local, [key]: value };
			if (host?.canWrite()) host.write(blockId, { ...local });
		},
		canFollowUp: () => Boolean(done && !parsed.error && host?.canFollowUp()),
		followUp: async (prompt: string) => {
			if (done && !parsed.error && host?.canFollowUp()) await host.followUp(prompt);
		}
	});
</script>

<div
	class="intelligent-ui not-prose my-4 overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm dark:border-gray-700 dark:bg-gray-900"
	data-intelligent-ui={blockId}
>
	<div
		class="flex items-center justify-between gap-3 border-b border-gray-100 px-4 py-2 text-xs text-gray-500 dark:border-gray-800 dark:text-gray-400"
	>
		<span class="flex items-center gap-2"
			><span class="h-1.5 w-1.5 rounded-full bg-indigo-500"></span>Interactive view</span
		>
		<button
			type="button"
			class="rounded px-2 py-1 hover:bg-gray-100 focus-visible:outline-indigo-500 dark:hover:bg-gray-800"
			onclick={() => (showSource = !showSource)}
			aria-expanded={showSource}>{showSource ? 'Hide data' : 'View data'}</button
		>
	</div>
	<div class="p-4 sm:p-5">
		{#if parsed.spec && !parsed.error}
			<CatalogRenderer spec={renderSpec} {registry} loading={!done} />
		{:else if parsed.error}
			<p class="text-sm text-gray-600 dark:text-gray-300">
				This interactive view could not be displayed. The original data is available below.
			</p>
		{:else}
			<p role="status" class="text-sm text-gray-500 dark:text-gray-400">
				Preparing interactive view…
			</p>
		{/if}
		{#if showSource || parsed.error}
			<pre
				class="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-gray-50 p-3 text-xs text-gray-700 dark:bg-gray-950 dark:text-gray-300">{source.slice(
					0,
					131072
				)}</pre>
		{/if}
	</div>
</div>

<script lang="ts">
	import { getContext, setContext } from 'svelte';
	import type { Readable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import { CatalogRenderer } from '@json-render/svelte';
	import { parseIntelligentUI, intelligentUIHash } from '$lib/utils/intelligent-ui';
	import { validInteractionValue } from '$lib/utils/intelligent-ui/state-value';
	import { resolveSharedValue } from '$lib/utils/intelligent-ui/data';
	import Element from './Element.svelte';
	import { EXTENDED_TYPES } from '$lib/utils/intelligent-ui/extended';
	import {
		INTELLIGENT_UI_HOST,
		INTELLIGENT_UI_BLOCK,
		type IntelligentUIHost,
		type InteractionValue,
		type InteractionState
	} from './context';

	let {
		source,
		done = false,
		blockId
	}: { source: string; done?: boolean; blockId: string } = $props();
	const host = getContext<IntelligentUIHost | undefined>(INTELLIGENT_UI_HOST);
	const i18n = getContext<Readable<I18n>>('i18n');
	let viewLabel = $derived($i18n.t('Interactive view'));
	let local = $state<InteractionState>({});
	let showSource = $state(false);
	const registry = {
		...Object.fromEntries(EXTENDED_TYPES.map((type) => [type, Element])),
		AppBlock: Element,
		WritingBlock: Element,
		CodeBlock: Element,
		Stack: Element,
		Grid: Element,
		Tabs: Element,
		DataTable: Element,
		MetricCard: Element,
		Select: Element,
		NumberInput: Element,
		Chart: Element,
		ComparisonCards: Element,
		Explanation: Element,
		ParameterSlider: Element
	};
	let parsed = $derived(parseIntelligentUI(source, done));
	const read = (key: string, fallback: InteractionValue): InteractionValue => {
		const value = local[key] ?? host?.read(blockId)?.[key];
		return typeof value === typeof fallback && validInteractionValue(key, value) ? value : fallback;
	};
	const write = (key: string, value: InteractionValue) => {
		if (!key || key.length > 512 || !validInteractionValue(key, value)) return;
		if (local[key] === value && (!host?.canWrite() || host.read(blockId)?.[key] === value)) return;
		local = { ...host?.read(blockId), ...local, [key]: value };
		if (host?.canWrite()) host.write(blockId, { ...local });
	};
	let renderSpec = $derived.by(() => {
		if (!parsed.spec) return null;
		return {
			...parsed.spec,
			elements: Object.fromEntries(
				Object.entries(parsed.spec.elements).map(([key, element]) => {
					const tabs =
						element.type === 'Tabs'
							? element.children.map((child, index) => ({
									id: child,
									label: element.props.labels[index]
								}))
							: undefined;
					const requested = tabs ? read(`tab:${key}`, tabs[0]?.id ?? '') : undefined;
					const selected = tabs?.find((tab) => tab.id === requested)?.id ?? tabs?.[0]?.id;
					const index =
						element.type === 'Carousel'
							? Math.min(
									element.children.length - 1,
									Math.max(0, Math.floor(Number(read(`carousel:${key}`, 0))))
								)
							: 0;
					const branch =
						element.type === 'Conditional'
							? resolveSharedValue(parsed.spec!, element.props.stateKey, read) ===
								element.props.equals
								? 0
								: 1
							: 0;
					const displayedChildren = tabs
						? selected
							? [selected]
							: []
						: element.type === 'Carousel'
							? element.children.slice(index, index + 1)
							: element.type === 'Conditional'
								? element.children.slice(branch, branch + 1)
								: element.children;
					return [
						key,
						{
							...element,
							children: displayedChildren,
							props: {
								...element.props,
								__key: key,
								__scope: intelligentUIHash(blockId),
								__index: index,
								__count: element.children.length,
								...(tabs ? { __tabs: tabs, __selected: selected } : {})
							}
						}
					];
				})
			)
		};
	});
	setContext(INTELLIGENT_UI_BLOCK, {
		isDone: () => done,
		spec: () => parsed.spec,
		read,
		write,
		shared: (stateKey: string) =>
			parsed.spec ? resolveSharedValue(parsed.spec, stateKey, read) : undefined,
		writeShared: (stateKey: string, value: InteractionValue) => {
			if (!parsed.spec) return;
			const normalized = resolveSharedValue(parsed.spec, stateKey, (key, fallback) =>
				key === `shared:${stateKey}` ? value : read(key, fallback)
			);
			if (normalized !== undefined) write(`shared:${stateKey}`, normalized);
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
			><span class="h-1.5 w-1.5 rounded-full bg-indigo-500"></span>{viewLabel}</span
		>
		<button
			type="button"
			class="rounded px-2 py-1 hover:bg-gray-100 focus-visible:outline-indigo-500 dark:hover:bg-gray-800"
			onclick={() => (showSource = !showSource)}
			aria-expanded={showSource}>{showSource ? $i18n.t('Hide data') : $i18n.t('View data')}</button
		>
	</div>
	<div class="p-4 sm:p-5">
		{#if parsed.spec && !parsed.error}
			<CatalogRenderer spec={renderSpec} {registry} loading={!done} />
		{:else if parsed.error}
			<p class="text-sm text-gray-600 dark:text-gray-300">
				{$i18n.t(
					'This interactive view could not be displayed. The original data is available below.'
				)}
			</p>
		{:else}
			<p role="status" class="text-sm text-gray-500 dark:text-gray-400">
				{$i18n.t('Preparing interactive view…')}
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

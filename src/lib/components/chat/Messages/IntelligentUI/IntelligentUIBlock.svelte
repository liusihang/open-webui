<script lang="ts">
	import './colors.css';
	import { resolvedAppearance, type Appearance } from '$lib/utils/intelligent-ui/colors';
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
	let local = $state<InteractionState>({});
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
		// Resolve presentation per rendered path, not per element ID: shared subtrees
		// can appear under differently colored parents. Persistence keeps original IDs.
		const surfaces = new Set([
			'Card',
			'Carousel',
			'Popover',
			'Flow',
			'Button',
			'Pressable',
			'Input',
			'Textarea',
			'Checkbox',
			'RadioGroup',
			'DatePicker',
			'SegmentedControl',
			'Chart',
			'DataTable',
			'MetricCard',
			'Explanation',
			'ParameterSlider',
			'Select',
			'NumberInput'
		]);
		const elements: Record<
			string,
			{ type: string; children: string[]; props: Record<string, unknown> }
		> = {};
		const render = (key: string, path: string, parent?: Appearance): string => {
			const element = parsed.spec!.elements[key];
			const own =
				'appearance' in element.props
					? (element.props.appearance as Appearance | undefined)
					: undefined;
			const appearance =
				parent || own || surfaces.has(element.type)
					? resolvedAppearance(
							surfaces.has(element.type)
								? {
										...(parent?.accent ? { accent: parent.accent } : {}),
										...(parent?.palette ? { palette: parent.palette } : {}),
										...own
									}
								: { ...parent, ...own },
							{ surface: surfaces.has(element.type), strong: element.type === 'Button' }
						)
					: undefined;
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
					? resolveSharedValue(parsed.spec!, element.props.stateKey, read) === element.props.equals
						? 0
						: 1
					: 0;
			const displayed = tabs
				? selected
					? [selected]
					: []
				: element.type === 'Carousel'
					? element.children.slice(index, index + 1)
					: element.type === 'Conditional'
						? element.children.slice(branch, branch + 1)
						: element.children;
			const renderKey = `${key}__${path}`;
			elements[renderKey] = {
				...element,
				children: displayed.map((child) =>
					render(child, `${path}_${element.children.indexOf(child)}`, appearance)
				),
				props: {
					...element.props,
					...(appearance ? { __appearance: appearance } : {}),
					__key: key,
					__scope: intelligentUIHash(`${blockId}:${path}`),
					__index: index,
					__count: element.children.length,
					...(tabs ? { __tabs: tabs, __selected: selected } : {})
				}
			};
			return renderKey;
		};
		const root = render(parsed.spec.root, '0');
		return { ...parsed.spec, root, elements };
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

<div class="intelligent-ui not-prose my-4 min-w-0" data-intelligent-ui={blockId}>
	{#if parsed.spec && !parsed.error}
		<CatalogRenderer spec={renderSpec} {registry} loading={!done} />
	{:else if parsed.error}
		<p role="status" class="text-sm text-gray-600 dark:text-gray-300">
			{$i18n.t('This view could not be displayed. Please ask for a text response.')}
		</p>
	{:else}
		<p role="status" class="text-sm text-gray-500 dark:text-gray-400">
			{$i18n.t('Preparing content…')}
		</p>
	{/if}
</div>

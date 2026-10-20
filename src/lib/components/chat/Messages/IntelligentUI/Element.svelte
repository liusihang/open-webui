<script lang="ts">
	import { getContext, type Snippet } from 'svelte';
	import type { Readable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import { appearanceStyle } from '$lib/utils/intelligent-ui/colors';
	import { EXTENDED_TYPES } from '$lib/utils/intelligent-ui/extended';
	import { resolveRichSeries, normalizeRichSeries } from '$lib/utils/intelligent-ui/rich-chart';
	import ExtendedElement from './ExtendedElement.svelte';
	import AppBlock from './AppBlock.svelte';
	import WritingBlock from './WritingBlock.svelte';
	import CodeContentBlock from './CodeContentBlock.svelte';
	import RichChart from './RichChart.svelte';
	import { chartData, chartPointCount, filteredRows } from '$lib/utils/intelligent-ui/data';
	import DataView from './DataView.svelte';
	import MatrixChart from './MatrixChart.svelte';
	import { INTELLIGENT_UI_BLOCK, type IntelligentUIBlockContext } from './context';

	let {
		element,
		children
	}: { element: { type: string; props: any; children?: string[] }; children?: Snippet } = $props();
	const context = getContext<IntelligentUIBlockContext>(INTELLIGENT_UI_BLOCK);
	const i18n = getContext<Readable<I18n>>('i18n');
	let componentProps = $derived({
		...element.props,
		appearance: element.props.__appearance ?? element.props.appearance
	});
	// Include the control definition so edited/regenerated content never inherits an incompatible value.
	let controlKey = $derived(
		JSON.stringify([
			componentProps.__key,
			element.type,
			componentProps.title,
			componentProps.min,
			componentProps.max,
			componentProps.step
		]).slice(0, 512)
	);
	let value = $derived(
		Math.min(
			componentProps.max,
			Math.max(
				componentProps.min,
				Number(
					componentProps.stateKey
						? (context.shared(componentProps.stateKey) ?? componentProps.value)
						: context.read(controlKey, componentProps.value)
				)
			)
		)
	);
	let numberDraft = $state<string | null>(null);
	let sending = $state(false);
	let sendError = $state(false);
	let data: any[] = $derived(
		element.type === 'Chart' && context.spec()
			? chartData(context.spec()!, componentProps, context.read)
			: []
	);
	let pointCount = $derived(
		element.type === 'Chart' && context.spec()
			? chartPointCount(context.spec()!, componentProps, context.read)
			: 0
	);
	let series = $derived(
		componentProps.series
			? componentProps.dataSource && context.spec()
				? resolveRichSeries(
						filteredRows(
							context.spec()!,
							componentProps.dataSource,
							componentProps.filters,
							context.read
						),
						componentProps.xField,
						componentProps.series,
						componentProps
					)
				: componentProps.series
			: undefined
	);
	let totalPoints = $derived.by(() => {
		if (!componentProps.series) return pointCount;
		if (!componentProps.dataSource)
			return componentProps.series.reduce((n: number, item: any) => n + item.data.length, 0);
		const rows = context.spec()
			? filteredRows(
					context.spec()!,
					componentProps.dataSource,
					componentProps.filters,
					context.read
				)
			: [];
		return componentProps.series.reduce(
			(n: number, item: any) =>
				n +
				rows.filter(
					(row) =>
						row[componentProps.xField] != null &&
						typeof row[item.yField] === 'number' &&
						Number.isFinite(row[item.yField])
				).length,
			0
		);
	});
	let shownPoints = $derived(
		series
			? normalizeRichSeries(data, series).reduce((n, item) => n + item.data.length, 0)
			: data.length
	);
	const format = (n: number) =>
		new Intl.NumberFormat(undefined, { maximumFractionDigits: 5 }).format(n);

	const writeNumber = (next: number) => {
		if (!Number.isFinite(next)) return;
		if (componentProps.stateKey) context.writeShared(componentProps.stateKey, next);
		else context.write(controlKey, next);
	};
	const commitNumber = () => {
		if (numberDraft !== null && numberDraft.trim() !== '') writeNumber(Number(numberDraft));
		numberDraft = null;
	};
	const changeTab = (event: KeyboardEvent, index: number) => {
		const tabs = componentProps.__tabs ?? [];
		let next = index;
		if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
		else if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
		else if (event.key === 'Home') next = 0;
		else if (event.key === 'End') next = tabs.length - 1;
		else return;
		event.preventDefault();
		context.write(`tab:${componentProps.__key}`, tabs[next].id);
		const list = (event.currentTarget as HTMLButtonElement).parentElement;
		(list?.querySelectorAll('button')[next] as HTMLButtonElement)?.focus();
	};

	async function followUp() {
		if (sending || !context.canFollowUp() || !componentProps.followUp) return;
		sending = true;
		sendError = false;
		try {
			await context.followUp(
				`${componentProps.followUp}\n\nSelected parameter: ${componentProps.title} = ${value}${componentProps.unit ? ` ${componentProps.unit}` : ''}.`
			);
		} catch {
			sendError = true;
		} finally {
			sending = false;
		}
	}
</script>

{#if EXTENDED_TYPES.includes(element.type as any)}
	<ExtendedElement {element} {children} />
{:else if element.type === 'AppBlock'}
	<AppBlock view={componentProps} />
{:else if element.type === 'WritingBlock'}
	<WritingBlock view={componentProps} />
{:else if element.type === 'CodeBlock'}
	<CodeContentBlock view={componentProps} />
{:else if element.type === 'Stack'}
	<section
		class="iui-ink space-y-6"
		class:iui-surface={Boolean(
			element.props.appearance?.background || element.props.appearance?.border
		)}
		class:iui-layout-surface={Boolean(
			element.props.appearance?.background || element.props.appearance?.border
		)}
		style={appearanceStyle(componentProps.appearance)}
	>
		{#if componentProps.title}<h3 class="text-base font-semibold tracking-tight">
				{componentProps.title}
			</h3>{/if}
		{#if children}{@render children()}{/if}
	</section>
{:else if element.type === 'Grid'}
	<section
		class="iui-ink space-y-4"
		class:iui-surface={Boolean(
			element.props.appearance?.background || element.props.appearance?.border
		)}
		class:iui-layout-surface={Boolean(
			element.props.appearance?.background || element.props.appearance?.border
		)}
		style={appearanceStyle(componentProps.appearance)}
	>
		{#if componentProps.title}<h3 class="text-base font-semibold tracking-tight">
				{componentProps.title}
			</h3>{/if}
		<div
			class={`grid grid-cols-1 gap-4 ${({ 1: '', 2: 'sm:grid-cols-2', 3: 'sm:grid-cols-2 lg:grid-cols-3', 4: 'sm:grid-cols-2 lg:grid-cols-4' } as Record<number, string>)[componentProps.columns]}`}
		>
			{#if children}{@render children()}{/if}
		</div>
	</section>
{:else if element.type === 'Tabs'}
	<section
		class="iui-ink min-w-0 space-y-4"
		class:iui-surface={Boolean(
			element.props.appearance?.background || element.props.appearance?.border
		)}
		class:iui-layout-surface={Boolean(
			element.props.appearance?.background || element.props.appearance?.border
		)}
		style={appearanceStyle(componentProps.appearance)}
	>
		{#if componentProps.title}<h3 class="text-sm font-semibold tracking-tight">
				{componentProps.title}
			</h3>{/if}
		<div
			role="tablist"
			aria-label={componentProps.title ?? $i18n.t('View options')}
			class="iui-surface flex gap-1 overflow-x-auto rounded-xl border p-1"
			style={appearanceStyle(componentProps.appearance, { surface: true })}
		>
			{#each componentProps.__tabs ?? [] as tab, index}<button
					type="button"
					role="tab"
					id={`${componentProps.__scope}-${componentProps.__key}-tab-${tab.id}`}
					aria-controls={`${componentProps.__scope}-${componentProps.__key}-panel`}
					aria-selected={componentProps.__selected === tab.id}
					tabindex={componentProps.__selected === tab.id ? 0 : -1}
					class={`iui-surface shrink-0 rounded-lg px-3 py-2 text-sm font-medium focus-visible:outline-indigo-500 ${componentProps.__selected === tab.id ? 'shadow-sm' : ''}`}
					style={appearanceStyle(
						{
							...componentProps.appearance,
							...(componentProps.__selected === tab.id
								? { background: componentProps.appearance?.accent ?? '#6366f1' }
								: {})
						},
						{ surface: true }
					)}
					onclick={() => context.write(`tab:${componentProps.__key}`, tab.id)}
					onkeydown={(event) => changeTab(event, index)}>{tab.label}</button
				>{/each}
		</div>
		<div
			role="tabpanel"
			tabindex="0"
			id={`${componentProps.__scope}-${componentProps.__key}-panel`}
			aria-labelledby={`${componentProps.__scope}-${componentProps.__key}-tab-${componentProps.__selected}`}
			class="min-w-0 rounded focus-visible:outline-indigo-500"
		>
			{#if children}{@render children()}{/if}
		</div>
	</section>
{:else if element.type === 'Select'}
	<label
		class="iui-surface block space-y-2 rounded-xl p-3"
		style={appearanceStyle(componentProps.appearance)}
		><span class="text-sm font-medium">{componentProps.title}</span><select
			value={String(context.shared(componentProps.stateKey) ?? componentProps.value)}
			class="iui-surface w-full rounded-xl border px-3 py-2.5 text-sm"
			style={appearanceStyle(componentProps.appearance, { surface: true })}
			onchange={(event) => context.writeShared(componentProps.stateKey, event.currentTarget.value)}
			>{#each componentProps.options as option}<option value={option.value}>{option.label}</option
				>{/each}</select
		></label
	>
{:else if element.type === 'NumberInput'}
	<label
		class="iui-surface block space-y-2 rounded-xl p-3"
		style={appearanceStyle(componentProps.appearance)}
		><span class="text-sm font-medium"
			>{componentProps.title}{componentProps.unit ? ` (${componentProps.unit})` : ''}</span
		><input
			type="number"
			min={componentProps.min}
			max={componentProps.max}
			step={componentProps.step}
			value={numberDraft ?? value}
			class="iui-surface w-full rounded-xl border px-3 py-2.5 text-sm tabular-nums"
			style={appearanceStyle(componentProps.appearance, { surface: true })}
			oninput={(event) => (numberDraft = event.currentTarget.value)}
			onchange={commitNumber}
			onblur={commitNumber}
			onkeydown={(event) => {
				if (event.key === 'Enter') commitNumber();
				else if (event.key === 'Escape') numberDraft = null;
			}}
		/></label
	>
{:else if element.type === 'DataTable' || element.type === 'MetricCard'}
	<DataView kind={element.type} view={componentProps} />
{:else if element.type === 'Chart' && (componentProps.kind === 'scatter' || componentProps.kind === 'heatmap')}
	<MatrixChart view={componentProps} {data} />
{:else if element.type === 'Chart'}
	<RichChart view={componentProps} {data} {series} />
{:else if element.type === 'ComparisonCards'}
	<section
		class="iui-ink space-y-4"
		class:iui-surface={Boolean(
			element.props.appearance?.background || element.props.appearance?.border
		)}
		class:iui-layout-surface={Boolean(
			element.props.appearance?.background || element.props.appearance?.border
		)}
		style={appearanceStyle(componentProps.appearance)}
	>
		<h3 class="text-sm font-semibold tracking-tight">{componentProps.title}</h3>
		<div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
			{#each componentProps.items as item}<article
					class="iui-surface rounded-2xl border p-5 shadow-sm"
					style={appearanceStyle(
						{ ...componentProps.appearance, ...item.appearance },
						{ surface: true }
					)}
				>
					<h4 class="text-sm font-semibold tracking-tight">{item.title}</h4>
					<p class="mt-2 whitespace-pre-wrap text-sm leading-relaxed">
						{item.description}
					</p>
					{#if item.attributes?.length}<dl class="mt-3 space-y-2">
							{#each item.attributes as attribute}<div
									class="flex flex-wrap justify-between gap-x-3 gap-y-1 text-xs"
								>
									<dt class="text-xs">{attribute.label}</dt>
									<dd class="font-medium">{attribute.value}</dd>
								</div>{/each}
						</dl>{/if}
				</article>{/each}
		</div>
	</section>
{:else if element.type === 'Explanation'}
	<details
		class="iui-surface rounded-2xl border px-4 py-3"
		style={appearanceStyle(componentProps.appearance, { surface: true })}
		open={Boolean(context.read(controlKey, false))}
		ontoggle={(event) => context.write(controlKey, event.currentTarget.open)}
	>
		<summary class="cursor-pointer rounded text-sm font-medium focus-visible:outline-indigo-500"
			>{componentProps.title}</summary
		>
		<p class="mt-3 whitespace-pre-wrap text-sm leading-relaxed">
			{componentProps.content}
		</p>
	</details>
{:else if element.type === 'ParameterSlider'}
	<section
		class="iui-surface space-y-4 rounded-2xl border p-5"
		style={appearanceStyle(componentProps.appearance, { surface: true })}
	>
		<label class="block"
			><span class="flex items-center justify-between gap-3 text-sm"
				><span class="font-medium">{componentProps.title}</span><span
					class="iui-accent rounded-md px-2 py-1 font-semibold tabular-nums"
					style={appearanceStyle(componentProps.appearance, { surface: true })}
					>{format(value)} {componentProps.unit ?? ''}</span
				></span
			>
			<input
				type="range"
				min={componentProps.min}
				max={componentProps.max}
				step={componentProps.step}
				{value}
				aria-valuetext={`${format(value)} ${componentProps.unit ?? ''}`}
				class="mt-4 w-full cursor-pointer"
				style="accent-color:var(--iui-accent)"
				oninput={(event) => writeNumber(Number(event.currentTarget.value))}
			/>
			<span class="mt-1 flex justify-between text-xs"
				><span>{format(componentProps.min)}</span><span>{format(componentProps.max)}</span></span
			>
		</label>
		<p class="text-xs">
			{componentProps.stateKey
				? $i18n.t('Updates linked views locally. No model request is sent.')
				: $i18n.t(
						'Adjusting this control stays local. It does not recalculate the model’s answer.'
					)}
		</p>
		{#if componentProps.followUp}
			<p class="whitespace-pre-wrap text-sm">
				<span class="font-medium">{$i18n.t('Follow-up question:')}</span>
				{componentProps.followUp}
			</p>
			<button
				type="button"
				onclick={followUp}
				disabled={!context.canFollowUp() || sending}
				class="iui-strong rounded-xl px-3 py-2 text-sm font-medium transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
				style={appearanceStyle(componentProps.appearance, { strong: true })}
				>{sending ? $i18n.t('Sending…') : $i18n.t('Ask model with this value')}</button
			>{/if}
		{#if sendError}<p role="alert" class="text-xs text-red-600 dark:text-red-400">
				{$i18n.t('The follow-up could not be sent. Please try again.')}
			</p>{/if}
	</section>
{/if}

{#if element.type === 'Chart' && totalPoints > shownPoints}
	<p class="mt-2 text-xs text-gray-500 dark:text-gray-400" role="status">
		{$i18n.t('Showing first {{shown}} of {{total}} matching points.', {
			shown: shownPoints,
			total: totalPoints
		})}
	</p>
{/if}

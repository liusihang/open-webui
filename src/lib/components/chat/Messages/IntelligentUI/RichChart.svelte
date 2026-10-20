<script lang="ts">
	import { getContext } from 'svelte';
	import type { Readable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import {
		normalizeRichSeries,
		pieSlices,
		chartScale,
		type RichPoint,
		type RichSeries,
		seriesIdentity,
		seriesColor,
		pointColor,
		type ChartColorOptions
	} from '$lib/utils/intelligent-ui/rich-chart';
	import {
		appearanceStyle,
		colorVariables,
		type Appearance
	} from '$lib/utils/intelligent-ui/colors';
	const i18n = getContext<Readable<I18n>>('i18n');
	let {
		view,
		data = [],
		series
	}: {
		view: ChartColorOptions & {
			appearance?: Appearance;
			title: string;
			kind: string;
			description?: string;
			unit?: string;
			donut?: boolean;
			__key?: string;
		};
		data?: RichPoint[];
		series?: RichSeries[];
	} = $props();
	const tooltipId = $props.id();
	let hidden = $state<string[]>([]);
	let active = $state('');
	let all = $derived(normalizeRichSeries(data, series, view.title));
	let visible = $derived(
		all
			.map((item, index) => ({ ...item, index }))
			.filter((item) => !hidden.includes(seriesIdentity(item)))
	);
	let scale = $derived(chartScale(visible));
	let labels = $derived(
		all.length === 1
			? all[0].data.map((point) => point.label)
			: [...new Set(all.flatMap((item) => item.data.map((point) => point.label)))]
	);
	let slices = $derived(pieSlices(all[0]?.data ?? []));
	const format = (value: number) =>
		new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 }).format(value);
	const axisFormat = (value: number) =>
		Math.abs(value) >= 1e9 || (value !== 0 && Math.abs(value) < 0.001)
			? value.toExponential(1)
			: new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(
					value
				);
	const axisLabel = (value: string) => {
		const characterWidth = [...value].some((character) => character.codePointAt(0)! > 127) ? 15 : 9;
		const limit = Math.max(
			2,
			Math.min(12, Math.floor(498 / Math.max(1, labels.length) / characterWidth) - 1)
		);
		const characters = [...value];
		return characters.length > limit ? characters.slice(0, limit - 1).join('') + '…' : value;
	};
	const label = (name: string, point: RichPoint) =>
		`${name ? name + ', ' : ''}${point.label}: ${format(point.value)}${view.unit ? ' ' + view.unit : ''}`;
	const x = (name: string, index = 0) =>
		72 +
		((all.length === 1 ? index : labels.indexOf(name)) + 0.5) * (498 / Math.max(1, labels.length));
	function navigate(event: KeyboardEvent) {
		if (event.key === 'Enter' || event.key === ' ') {
			event.preventDefault();
			active = (event.currentTarget as SVGElement).getAttribute('aria-label') ?? '';
			return;
		}
		if (event.key === 'Escape') {
			active = '';
			return;
		}
		if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key))
			return;
		const target = event.currentTarget as SVGElement;
		const points = Array.from(
			target.closest('svg')?.querySelectorAll<SVGElement>('[data-chart-point]') ?? []
		);
		const current = points.indexOf(target);
		const next =
			event.key === 'Home'
				? 0
				: event.key === 'End'
					? points.length - 1
					: (current + (['ArrowLeft', 'ArrowUp'].includes(event.key) ? -1 : 1) + points.length) %
						points.length;
		event.preventDefault();
		points[next]?.focus();
	}
</script>

<section
	class="iui-surface min-w-0 space-y-3 rounded-2xl border p-4 sm:p-5"
	style={appearanceStyle(view.appearance, { surface: true })}
	data-chart={view.__key}
>
	<h3 class="text-sm font-semibold tracking-tight">{view.title}</h3>
	{#if view.description}<p class="text-sm leading-relaxed">
			{view.description}
		</p>{/if}
	{#if all.length && (view.kind !== 'pie' || slices.length)}
		<!-- The scrollable region must be keyboard reachable. -->
		<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
		<div
			class="chart-viewport"
			tabindex={view.kind === 'pie' ? undefined : 0}
			role="region"
			aria-label={view.title}
		>
			<svg
				viewBox="0 0 600 245"
				role="group"
				aria-label={view.title}
				class="chart-plot"
				class:chart-pie={view.kind === 'pie'}
			>
				<title>{view.title}</title>
				{#if view.kind === 'pie'}
					{#each slices.filter((slice) => slice.value > 0) as slice}
						<path
							d={slice.path}
							class="chart-fill chart-slice"
							style={colorVariables(pointColor(slice, view), 'chart')}
							stroke-width="1"
							tabindex="0"
							role="button"
							data-chart-point
							aria-describedby={active ? tooltipId : undefined}
							aria-label={`${label('', slice)} (${format(slice.fraction * 100)}%)`}
							onfocus={() => (active = `${label('', slice)} (${format(slice.fraction * 100)}%)`)}
							onblur={() => (active = '')}
							onpointerenter={() =>
								(active = `${label('', slice)} (${format(slice.fraction * 100)}%)`)}
							onpointerleave={() => (active = '')}
							onclick={(event) => (active = event.currentTarget.getAttribute('aria-label') ?? '')}
							onkeydown={navigate}><title>{label('', slice)}</title></path
						>
					{/each}
					{#if view.donut}<circle
							cx="300"
							cy="115"
							r="48"
							class="chart-donut"
							pointer-events="none"
						/>{/if}
				{:else}
					<path
						d={`M 68 25 V 205 H 575 M 68 ${scale.y(0)} H 575`}
						fill="none"
						stroke="currentColor"
						opacity="0.2"
					/>
					{#each [25, 70, 115, 160] as gridY}<path
							d={`M 68 ${gridY} H 575`}
							fill="none"
							stroke="currentColor"
							opacity="0.07"
						/>{/each}
					<text x="62" y="29" text-anchor="end" font-size="15" fill="currentColor"
						>{axisFormat(scale.max)}</text
					>
					<text x="62" y="209" text-anchor="end" font-size="15" fill="currentColor"
						>{axisFormat(scale.min)}</text
					>
					{#each visible as item, visibleIndex}
						{#if view.kind === 'line'}<polyline
								points={item.data
									.map((point, index) => `${x(point.label, index)},${scale.y(point.value)}`)
									.join(' ')}
								fill="none"
								class="chart-stroke"
								style={colorVariables(seriesColor(item, view), 'chart')}
								stroke-width="2.5"
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-dasharray={item.index % 2 ? '6 3' : undefined}
							/>{/if}
						{#each item.data as point, pointIndex}
							{@const description = label(all.length > 1 ? item.name : '', point)}
							<g
								tabindex="0"
								role="button"
								data-chart-point
								aria-describedby={active ? tooltipId : undefined}
								aria-label={description}
								onfocus={() => (active = description)}
								onblur={() => (active = '')}
								onpointerenter={() => (active = description)}
								onpointerleave={() => (active = '')}
								onclick={(event) => (active = event.currentTarget.getAttribute('aria-label') ?? '')}
								onkeydown={navigate}
							>
								<title>{description}</title>
								{#if view.kind === 'line'}<circle
										cx={x(point.label, pointIndex)}
										cy={scale.y(point.value)}
										r="5"
										class="chart-fill"
										style={colorVariables(pointColor(point, view, item), 'chart')}
									/>
								{:else}{@const width =
										((498 / Math.max(1, labels.length)) * 0.8) / visible.length}<rect
										x={x(point.label, pointIndex) -
											(width * visible.length) / 2 +
											visibleIndex * width}
										y={Math.min(scale.y(0), scale.y(point.value))}
										width={Math.max(0.5, width - 1)}
										height={Math.max(1, Math.abs(scale.y(point.value) - scale.y(0)))}
										rx="2"
										class="chart-fill"
										style={colorVariables(pointColor(point, view, item), 'chart')}
									/>{/if}
							</g>
						{/each}
					{/each}
					{#if labels.length <= 12}{#each labels as name, labelIndex}<text
								x={x(name, labelIndex)}
								y="230"
								text-anchor="middle"
								font-size="15"
								fill="currentColor">{axisLabel(name)}</text
							>{/each}{/if}
				{/if}
			</svg>
		</div>
		<div
			id={tooltipId}
			role="tooltip"
			aria-live="polite"
			aria-atomic="true"
			style={appearanceStyle(view.appearance, { surface: true })}
			class="iui-surface min-h-8 rounded-lg border px-3 py-1.5 text-xs tabular-nums transition-opacity {active
				? 'opacity-100'
				: 'opacity-0'}"
			data-chart-tooltip
		>
			{active}
		</div>
		{#if view.kind === 'pie'}
			<ul class="flex flex-wrap gap-x-4 gap-y-2 text-xs">
				{#each slices as slice}<li>
						<span
							aria-hidden="true"
							class="chart-ink"
							style={colorVariables(pointColor(slice, view), 'chart')}>●</span
						>
						{slice.label}: {format(slice.value)}
					</li>{/each}
			</ul>
		{:else if all.length > 1}
			<div class="flex flex-wrap gap-2" aria-label={$i18n.t('Series')}>
				{#each all as item}<button
						type="button"
						class="rounded-lg border border-black/10 px-2.5 py-1.5 text-xs transition-opacity focus-visible:outline-indigo-500 dark:border-white/15 {hidden.includes(
							seriesIdentity(item)
						)
							? 'line-through'
							: 'opacity-100'}"
						aria-pressed={!hidden.includes(seriesIdentity(item))}
						onclick={() => {
							hidden = hidden.includes(seriesIdentity(item))
								? hidden.filter((value) => value !== seriesIdentity(item))
								: [...hidden, seriesIdentity(item)];
							active = '';
						}}
						><span
							aria-hidden="true"
							class="chart-ink"
							style={colorVariables(seriesColor(item, view), 'chart')}>●</span
						>
						{item.name}</button
					>{/each}
			</div>
		{/if}
	{:else}<p class="rounded-lg bg-gray-50 p-4 text-sm text-gray-500 dark:bg-gray-800">
			{$i18n.t('No matching chart data')}
		</p>{/if}
	<details class="text-xs">
		<summary class="cursor-pointer rounded focus-visible:outline-indigo-500"
			>{$i18n.t('View values')}{view.unit ? ` (${view.unit})` : ''}</summary
		>
		<div class="mt-2 max-h-60 overflow-auto">
			<table class="w-full text-left">
				<thead
					><tr
						>{#if all.length > 1}<th scope="col">{$i18n.t('Series')}</th>{/if}<th scope="col"
							>{$i18n.t('Label')}</th
						><th scope="col">{$i18n.t('Value')}</th></tr
					></thead
				><tbody
					>{#each all as item}{#each item.data as point}<tr
								class="border-t border-gray-100 dark:border-gray-800"
								>{#if all.length > 1}<td>{item.name}</td>{/if}<th
									scope="row"
									class="py-1 font-normal">{point.label}</th
								><td class="tabular-nums">{format(point.value)}</td></tr
							>{/each}{/each}</tbody
				>
			</table>
		</div>
	</details>
</section>

<style>
	.chart-viewport {
		max-width: 100%;
		min-width: 0;
		overflow-x: auto;
		overscroll-behavior-x: contain;
		border-radius: 0.5rem;
	}
	.chart-viewport:focus-visible {
		outline: 2px solid currentColor;
		outline-offset: 2px;
	}
	.chart-plot {
		display: block;
		width: 100%;
		min-width: 480px;
	}
	.chart-pie {
		min-width: 0;
	}

	.chart-fill {
		fill: var(--chart-light);
	}
	.chart-stroke {
		stroke: var(--chart-light);
	}
	.chart-ink {
		color: var(--chart-light);
	}
	.chart-slice {
		stroke: var(--iui-bg-light, white);
	}
	.chart-donut {
		fill: var(--iui-bg-light, white);
	}
	:global(.dark) .chart-fill {
		fill: var(--chart-dark);
	}
	:global(.dark) .chart-stroke {
		stroke: var(--chart-dark);
	}
	:global(.dark) .chart-ink {
		color: var(--chart-dark);
	}
	:global(.dark) .chart-slice {
		stroke: var(--iui-bg-dark, #111827);
	}
	:global(.dark) .chart-donut {
		fill: var(--iui-bg-dark, #111827);
	}
	[data-chart-point]:focus {
		outline: none;
		filter: drop-shadow(0 0 3px currentColor);
		stroke: currentColor;
		stroke-width: 2;
	}
</style>

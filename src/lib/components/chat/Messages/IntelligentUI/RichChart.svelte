<script lang="ts">
	import { getContext } from 'svelte';
	import type { Readable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import {
		normalizeRichSeries,
		pieSlices,
		chartScale,
		type RichPoint,
		type RichSeries
	} from '$lib/utils/intelligent-ui/rich-chart';
	const i18n = getContext<Readable<I18n>>('i18n');
	let {
		view,
		data = [],
		series
	}: {
		view: {
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
	const colors = [
		'#6366f1',
		'#059669',
		'#db2777',
		'#d97706',
		'#0284c7',
		'#9333ea',
		'#dc2626',
		'#0d9488'
	];
	let hidden = $state<string[]>([]);
	let active = $state('');
	let all = $derived(normalizeRichSeries(data, series, view.title));
	let visible = $derived(
		all.map((item, index) => ({ ...item, index })).filter((item) => !hidden.includes(item.name))
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
	const label = (name: string, point: RichPoint) =>
		`${name ? name + ', ' : ''}${point.label}: ${format(point.value)}${view.unit ? ' ' + view.unit : ''}`;
	const x = (name: string, index = 0) =>
		50 +
		((all.length === 1 ? index : labels.indexOf(name)) + 0.5) * (510 / Math.max(1, labels.length));
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

<section class="min-w-0 space-y-3" data-chart={view.__key}>
	<h3 class="text-sm font-semibold text-gray-900 dark:text-gray-100">{view.title}</h3>
	{#if view.description}<p class="text-sm text-gray-500 dark:text-gray-400">
			{view.description}
		</p>{/if}
	{#if all.length && (view.kind !== 'pie' || slices.length)}
		<svg
			viewBox="0 0 600 245"
			role="group"
			aria-label={view.title}
			class="w-full text-gray-500 dark:text-gray-400"
		>
			<title>{view.title}</title>
			{#if view.kind === 'pie'}
				{#each slices.filter((slice) => slice.value > 0) as slice}
					<path
						d={slice.path}
						fill={colors[slice.index % colors.length]}
						stroke="white"
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
						class="fill-white dark:fill-gray-900"
						pointer-events="none"
					/>{/if}
			{:else}
				<path
					d={`M 45 25 V 205 H 565 M 45 ${scale.y(0)} H 565`}
					fill="none"
					stroke="currentColor"
					opacity="0.3"
				/>
				<text x="40" y="20" text-anchor="end" font-size="10" fill="currentColor"
					>{format(scale.max)}</text
				>
				<text x="40" y="215" text-anchor="end" font-size="10" fill="currentColor"
					>{format(scale.min)}</text
				>
				{#each visible as item, visibleIndex}
					{#if view.kind === 'line'}<polyline
							points={item.data
								.map((point, index) => `${x(point.label, index)},${scale.y(point.value)}`)
								.join(' ')}
							fill="none"
							stroke={colors[item.index]}
							stroke-width="2"
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
									fill={colors[item.index]}
								/>
							{:else}{@const width =
									((510 / Math.max(1, labels.length)) * 0.8) / visible.length}<rect
									x={x(point.label, pointIndex) -
										(width * visible.length) / 2 +
										visibleIndex * width}
									y={Math.min(scale.y(0), scale.y(point.value))}
									width={Math.max(0.5, width - 1)}
									height={Math.max(1, Math.abs(scale.y(point.value) - scale.y(0)))}
									rx="2"
									fill={colors[item.index]}
								/>{/if}
						</g>
					{/each}
				{/each}
				{#if labels.length <= 12}{#each labels as name, labelIndex}<text
							x={x(name, labelIndex)}
							y="230"
							text-anchor="middle"
							font-size="10"
							fill="currentColor">{name.length > 12 ? name.slice(0, 11) + '…' : name}</text
						>{/each}{/if}
			{/if}
		</svg>
		<div
			id={tooltipId}
			role="tooltip"
			aria-live="polite"
			aria-atomic="true"
			class="min-h-6 rounded bg-gray-50 px-2 text-sm dark:bg-gray-800"
			data-chart-tooltip
		>
			{active}
		</div>
		{#if view.kind === 'pie'}
			<ul class="flex flex-wrap gap-3 text-xs">
				{#each slices as slice}<li>
						<span aria-hidden="true" style={`color:${colors[slice.index % colors.length]}`}>●</span>
						{slice.label}: {format(slice.value)}
					</li>{/each}
			</ul>
		{:else if all.length > 1}
			<div class="flex flex-wrap gap-2" aria-label={$i18n.t('Series')}>
				{#each all as item, index}<button
						type="button"
						class="rounded border px-2 py-1 text-xs focus-visible:outline-indigo-500"
						aria-pressed={!hidden.includes(item.name)}
						onclick={() => {
							hidden = hidden.includes(item.name)
								? hidden.filter((value) => value !== item.name)
								: [...hidden, item.name];
							active = '';
						}}
						><span aria-hidden="true" style={`color:${colors[index]}`}>●</span> {item.name}</button
					>{/each}
			</div>
		{/if}
	{:else}<p class="rounded-lg bg-gray-50 p-4 text-sm text-gray-500 dark:bg-gray-800">
			{$i18n.t('No matching chart data')}
		</p>{/if}
	<details class="text-xs text-gray-500 dark:text-gray-400">
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
	[data-chart-point]:focus {
		outline: none;
		filter: drop-shadow(0 0 3px currentColor);
		stroke: currentColor;
		stroke-width: 2;
	}
</style>

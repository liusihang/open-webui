<script lang="ts">
	import { getContext } from 'svelte';
	import type { Readable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import {
		AXIS_FONT_SIZE,
		AXIS_LINE_HEIGHT,
		categoricalChartLayout
	} from '$lib/utils/intelligent-ui/chart-layout';
	import { appearanceStyle, colorVariables, isColorValue } from '$lib/utils/intelligent-ui/colors';
	import {
		chartMetadata,
		customPointColor,
		pointColor,
		type ChartColorOptions,
		type ChartColorMetadata
	} from '$lib/utils/intelligent-ui/rich-chart';
	const i18n = getContext<Readable<I18n>>('i18n');
	type MatrixPoint = {
		x: string | number;
		y: string | number;
		label?: string;
		value?: number;
	} & ChartColorMetadata;
	let {
		view,
		data: inputData
	}: {
		view: {
			title: string;
			kind: 'scatter' | 'heatmap';
			description?: string;
			unit?: string;
			__key?: string;
		} & ChartColorOptions;
		data: MatrixPoint[];
	} = $props();
	const tooltipId = $props.id();
	let active = $state('');
	let viewportWidth = $state(600);
	let data = $derived(
		inputData
			.slice(0, 128)
			.filter(
				(point) =>
					point &&
					(view.kind === 'scatter'
						? Number.isFinite(point.x) && Number.isFinite(point.y)
						: typeof point.x === 'string' &&
							typeof point.y === 'string' &&
							Number.isFinite(point.value))
			)
			.map((point) => ({ ...point, id: undefined, color: undefined, ...chartMetadata(point) }))
	);
	let minX = $derived(Math.min(0, ...data.map((point) => Number(point.x)).filter(Number.isFinite)));
	let maxX = $derived(Math.max(0, ...data.map((point) => Number(point.x)).filter(Number.isFinite)));
	let minY = $derived(Math.min(0, ...data.map((point) => Number(point.y)).filter(Number.isFinite)));
	let maxY = $derived(Math.max(0, ...data.map((point) => Number(point.y)).filter(Number.isFinite)));
	let xLabels = $derived([...new Set<string>(data.map((point) => String(point.x)))]);
	let yLabels = $derived([...new Set<string>(data.map((point) => String(point.y)))]);
	let layout = $derived(
		categoricalChartLayout(
			view.kind === 'heatmap' ? xLabels : [],
			viewportWidth,
			view.kind === 'heatmap' ? yLabels : []
		)
	);
	let minValue = $derived(Math.min(0, ...data.map((point) => point.value ?? 0)));
	let maxValue = $derived(Math.max(0, ...data.map((point) => point.value ?? 0)));
	const ratio = (value: number, min: number, max: number) => {
		const magnitude = Math.max(1, Math.abs(min), Math.abs(max));
		return (value / magnitude - min / magnitude) / (max / magnitude - min / magnitude || 1);
	};
	const x = (value: number) => layout.left + ratio(value, minX, maxX) * layout.plotWidth;
	const y = (value: number) => 200 - ratio(value, minY, maxY) * 170;
	const format = (value: number) =>
		new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 }).format(value);
	const axisFormat = (value: number) =>
		Math.abs(value) >= 1e9 || (value !== 0 && Math.abs(value) < 0.001)
			? value.toExponential(1)
			: new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(
					value
				);
	const description = (point: MatrixPoint) =>
		view.kind === 'scatter'
			? `${point.label ? point.label + ': ' : ''}${format(Number(point.x))}, ${format(Number(point.y))}${view.unit ? ' ' + view.unit : ''}`
			: `${point.x}, ${point.y}: ${format(point.value ?? 0)}${view.unit ? ' ' + view.unit : ''}`;
	const heatHex = (hue: number, lightness: number) => {
		const light = lightness / 100;
		const amplitude = 0.7 * Math.min(light, 1 - light);
		const channel = (offset: number) => {
			const segment = (offset + hue / 30) % 12;
			return Math.round(
				255 * (light - amplitude * Math.max(-1, Math.min(segment - 3, 9 - segment, 1)))
			)
				.toString(16)
				.padStart(2, '0');
		};
		return `#${channel(0)}${channel(8)}${channel(4)}`;
	};
	const pointStyle = (point: MatrixPoint) => {
		if (view.kind === 'scatter' || customPointColor(point, view))
			return colorVariables(pointColor(point, view), 'chart');
		const palette = view.palette ?? view.appearance?.palette;
		const hue =
			palette === 'ocean' ? 200 : palette === 'forest' ? 150 : palette === 'sunset' ? 25 : 245;
		const strength = ratio(point.value ?? 0, minValue, maxValue);
		if (isColorValue(view.appearance?.background)) {
			const color = {
				light: heatHex(hue, 88 - strength * 48),
				dark: heatHex(hue, 24 + strength * 42)
			};
			return colorVariables(pointColor({ ...point, color }, view), 'chart');
		}
		return `--chart-light:hsl(${hue} 70% ${88 - strength * 48}%);--chart-dark:hsl(${hue} 70% ${24 + strength * 42}%)`;
	};
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
	{#if data.length}
		<!-- The scrollable region must be keyboard reachable. -->
		<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
		<div
			class="chart-viewport"
			bind:clientWidth={viewportWidth}
			tabindex="0"
			role="region"
			aria-label={view.title}
		>
			<svg
				viewBox={`0 0 ${layout.width} ${layout.height}`}
				width={layout.width}
				height={layout.height}
				style:width={`${layout.width}px`}
				style:height={`${layout.height}px`}
				role="group"
				aria-label={view.title}
				class="chart-plot"
				><title>{view.title}</title><desc
					>{data
						.map(
							(point) =>
								`${point.x}, ${point.y}${point.value !== undefined ? ': ' + point.value : ''}`
						)
						.join('; ')}</desc
				>
				{#if view.kind === 'scatter'}
					<path
						d={`M 68 30 V 200 H ${layout.width - 24}`}
						fill="none"
						stroke="currentColor"
						opacity="0.2"
					/>
					{#each data as point}<circle
							tabindex="0"
							role="button"
							data-chart-point
							aria-label={description(point)}
							aria-describedby={active ? tooltipId : undefined}
							onfocus={() => (active = description(point))}
							onblur={() => (active = '')}
							onpointerenter={() => (active = description(point))}
							onpointerleave={() => (active = '')}
							onclick={() => (active = description(point))}
							onkeydown={navigate}
							cx={x(Number(point.x))}
							cy={y(Number(point.y))}
							r="5"
							class="chart-fill"
							style={pointStyle(point)}
							><title
								>{point.label ? `${point.label}: ` : ''}{format(Number(point.x))}, {format(
									Number(point.y)
								)}</title
							></circle
						>{/each}
					<text x="68" y="222" font-size={AXIS_FONT_SIZE} fill="currentColor"
						>{axisFormat(minX)}</text
					><text
						x={layout.width - 24}
						y="222"
						text-anchor="end"
						font-size={AXIS_FONT_SIZE}
						fill="currentColor">{axisFormat(maxX)}</text
					><text x="62" y="35" text-anchor="end" font-size={AXIS_FONT_SIZE} fill="currentColor"
						>{axisFormat(maxY)}</text
					><text x="62" y="200" text-anchor="end" font-size={AXIS_FONT_SIZE} fill="currentColor"
						>{axisFormat(minY)}</text
					>
				{:else}
					{#each data as point}<rect
							tabindex="0"
							role="button"
							data-chart-point
							aria-label={description(point)}
							aria-describedby={active ? tooltipId : undefined}
							onfocus={() => (active = description(point))}
							onblur={() => (active = '')}
							onpointerenter={() => (active = description(point))}
							onpointerleave={() => (active = '')}
							onclick={() => (active = description(point))}
							onkeydown={navigate}
							x={layout.left + xLabels.indexOf(String(point.x)) * layout.columnWidth}
							y={25 + yLabels.indexOf(String(point.y)) * (layout.plotHeight / yLabels.length)}
							width={Math.max(1, layout.columnWidth - 2)}
							height={Math.max(1, layout.plotHeight / yLabels.length - 2)}
							rx="2"
							class="chart-fill"
							style={pointStyle(point)}
							><title>{point.x}, {point.y}: {format(point.value ?? 0)}</title></rect
						>{/each}
					{#each xLabels as label, index}<text
							data-axis="x"
							aria-label={label}
							x={layout.left + (index + 0.5) * layout.columnWidth}
							y={layout.bottom + 20}
							text-anchor="end"
							transform={`rotate(-45 ${layout.left + (index + 0.5) * layout.columnWidth} ${layout.bottom + 20})`}
							font-size={AXIS_FONT_SIZE}
							fill="currentColor">{label}</text
						>{/each}
					{#each yLabels as label, index}<text
							data-axis="y"
							aria-label={label}
							x={layout.left - 8}
							y={25 +
								(index + 0.5) * (layout.plotHeight / yLabels.length) -
								((layout.yLines[index].length - 1) * AXIS_LINE_HEIGHT) / 2 +
								4}
							text-anchor="end"
							font-size={AXIS_FONT_SIZE}
							fill="currentColor"
							>{#each layout.yLines[index] as line, lineIndex}<tspan
									x={layout.left - 8}
									dy={lineIndex ? AXIS_LINE_HEIGHT : 0}>{line}</tspan
								>{/each}</text
						>{/each}
				{/if}
			</svg>
		</div>
		<div
			id={tooltipId}
			role="tooltip"
			aria-live="polite"
			aria-atomic="true"
			data-chart-tooltip
			style={appearanceStyle(view.appearance, { surface: true })}
			class="iui-surface min-h-8 rounded-lg border px-3 py-1.5 text-xs tabular-nums transition-opacity {active
				? 'opacity-100'
				: 'opacity-0'}"
		>
			{active}
		</div>
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
						><th scope="col">X</th><th scope="col">Y</th>{#if view.kind === 'heatmap'}<th
								scope="col">{$i18n.t('Value')}</th
							>{/if}</tr
					></thead
				><tbody
					>{#each data as point}<tr class="border-t border-gray-100 dark:border-gray-800"
							><td class="py-1">{point.x}</td><td class="py-1">{point.y}</td
							>{#if view.kind === 'heatmap'}<td class="py-1 tabular-nums"
									>{format(point.value ?? 0)}</td
								>{/if}</tr
						>{/each}</tbody
				>
			</table>
		</div>
	</details>
</section>

<style>
	.chart-viewport {
		max-width: 100%;
		min-width: 0;
		max-height: 32rem;
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
		max-width: none;
	}

	.chart-fill {
		fill: var(--chart-light);
	}
	:global(.dark) .chart-fill {
		fill: var(--chart-dark);
	}
	[data-chart-point]:focus {
		outline: none;
		filter: drop-shadow(0 0 3px currentColor);
		stroke: currentColor;
		stroke-width: 2;
	}
</style>

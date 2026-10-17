<script lang="ts">
	let { view, data }: { view: any; data: any[] } = $props();
	let minX = $derived(Math.min(0, ...data.map((point) => Number(point.x)).filter(Number.isFinite)));
	let maxX = $derived(Math.max(0, ...data.map((point) => Number(point.x)).filter(Number.isFinite)));
	let minY = $derived(Math.min(0, ...data.map((point) => Number(point.y)).filter(Number.isFinite)));
	let maxY = $derived(Math.max(0, ...data.map((point) => Number(point.y)).filter(Number.isFinite)));
	let xLabels = $derived([...new Set(data.map((point) => String(point.x)))]);
	let yLabels = $derived([...new Set(data.map((point) => String(point.y)))]);
	let minValue = $derived(Math.min(0, ...data.map((point) => point.value ?? 0)));
	let maxValue = $derived(Math.max(0, ...data.map((point) => point.value ?? 0)));
	const x = (value: number) => 40 + ((value - minX) / (maxX - minX || 1)) * 520;
	const y = (value: number) => 200 - ((value - minY) / (maxY - minY || 1)) * 170;
	const format = (value: number) =>
		new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 }).format(value);
</script>

<section class="min-w-0 space-y-3" data-chart={view.__key}>
	<h3 class="text-sm font-semibold text-gray-900 dark:text-gray-100">{view.title}</h3>
	{#if view.description}<p class="text-sm text-gray-500 dark:text-gray-400">
			{view.description}
		</p>{/if}
	{#if data.length}
		<svg
			viewBox="0 0 600 245"
			role="img"
			aria-label={view.title}
			class="w-full text-indigo-500 dark:text-indigo-400"
			><title>{view.title}</title><desc
				>{data
					.map(
						(point) =>
							`${point.x}, ${point.y}${point.value !== undefined ? ': ' + point.value : ''}`
					)
					.join('; ')}</desc
			>
			{#if view.kind === 'scatter'}
				<path d="M 40 30 V 200 H 560" fill="none" stroke="currentColor" opacity="0.3" />
				{#each data as point}<circle
						cx={x(point.x)}
						cy={y(point.y)}
						r="5"
						fill="currentColor"
						opacity="0.8"
						><title
							>{point.label ? `${point.label}: ` : ''}{format(point.x)}, {format(point.y)}</title
						></circle
					>{/each}
				<text x="40" y="222" font-size="11" fill="currentColor">{format(minX)}</text><text
					x="560"
					y="222"
					text-anchor="end"
					font-size="11"
					fill="currentColor">{format(maxX)}</text
				><text x="35" y="35" text-anchor="end" font-size="10" fill="currentColor"
					>{format(maxY)}</text
				><text x="35" y="200" text-anchor="end" font-size="10" fill="currentColor"
					>{format(minY)}</text
				>
			{:else}
				{#each data as point}<rect
						x={40 + xLabels.indexOf(String(point.x)) * (520 / xLabels.length)}
						y={25 + yLabels.indexOf(String(point.y)) * (175 / yLabels.length)}
						width={Math.max(1, 520 / xLabels.length - 2)}
						height={Math.max(1, 175 / yLabels.length - 2)}
						rx="2"
						fill={`hsl(245 70% ${88 - ((point.value - minValue) / (maxValue - minValue || 1)) * 48}%)`}
						><title>{point.x}, {point.y}: {format(point.value)}</title></rect
					>{/each}
				{#if xLabels.length <= 10}{#each xLabels as label, index}<text
							x={40 + (index + 0.5) * (520 / xLabels.length)}
							y="220"
							text-anchor="middle"
							font-size="10"
							fill="currentColor">{label.length > 10 ? label.slice(0, 9) + '…' : label}</text
						>{/each}{/if}
				{#if yLabels.length <= 10}{#each yLabels as label, index}<text
							x="35"
							y={25 + (index + 0.5) * (175 / yLabels.length)}
							text-anchor="end"
							font-size="10"
							fill="currentColor">{label.length > 6 ? label.slice(0, 5) + '…' : label}</text
						>{/each}{/if}
			{/if}
		</svg>
	{:else}<p class="rounded-lg bg-gray-50 p-4 text-sm text-gray-500 dark:bg-gray-800">
			No matching chart data
		</p>{/if}
	<details class="text-xs text-gray-500 dark:text-gray-400">
		<summary class="cursor-pointer rounded focus-visible:outline-indigo-500"
			>View values{view.unit ? ` (${view.unit})` : ''}</summary
		>
		<div class="mt-2 max-h-60 overflow-auto">
			<table class="w-full text-left">
				<thead
					><tr
						><th scope="col">X</th><th scope="col">Y</th>{#if view.kind === 'heatmap'}<th
								scope="col">Value</th
							>{/if}</tr
					></thead
				><tbody
					>{#each data as point}<tr class="border-t border-gray-100 dark:border-gray-800"
							><td class="py-1">{point.x}</td><td class="py-1">{point.y}</td
							>{#if view.kind === 'heatmap'}<td class="py-1 tabular-nums">{format(point.value)}</td
								>{/if}</tr
						>{/each}</tbody
				>
			</table>
		</div>
	</details>
</section>

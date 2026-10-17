<script lang="ts">
	import { getContext } from 'svelte';
	import type { Readable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import { metricValue, tableRows } from '$lib/utils/intelligent-ui/data';
	import { INTELLIGENT_UI_BLOCK, type IntelligentUIBlockContext } from './context';
	let { kind, view }: { kind: string; view: any } = $props();
	const context = getContext<IntelligentUIBlockContext>(INTELLIGENT_UI_BLOCK);
	const i18n = getContext<Readable<I18n>>('i18n');
	const format = (value: unknown) =>
		value === null || value === undefined
			? '—'
			: typeof value === 'number'
				? new Intl.NumberFormat(undefined, { maximumFractionDigits: 5 }).format(value)
				: String(value);
	let search = $derived(String(context.read(`search:${view.__key}`, '')));
	let sortValue = $derived(String(context.read(`sort:${view.__key}`, '')));
	let sort = $derived.by(() => {
		const [direction, field] = sortValue.split(':');
		return (direction === 'asc' || direction === 'desc') &&
			view.columns?.some((column: any) => column.key === field)
			? ({ field, direction } as { field: string; direction: 'asc' | 'desc' })
			: undefined;
	});
	let rows = $derived(
		kind === 'DataTable' && context.spec()
			? tableRows(
					context.spec()!,
					view,
					context.read,
					view.searchable ? search : '',
					view.sortable ? sort : undefined
				)
			: []
	);
	let metric = $derived(
		kind === 'MetricCard' && context.spec()
			? metricValue(context.spec()!, view, context.read)
			: null
	);
	let selected = $derived(view.selectionKey ? context.shared(view.selectionKey) : '');
	const sortColumn = (field: string) =>
		context.write(
			`sort:${view.__key}`,
			`${sort?.field === field && sort?.direction === 'asc' ? 'desc' : 'asc'}:${field}`
		);
</script>

{#if kind === 'MetricCard'}
	<section
		class="rounded-xl border border-gray-200 bg-gray-50/70 p-4 dark:border-gray-700 dark:bg-gray-800/40"
		data-metric={view.__key}
	>
		<h3 class="text-sm font-medium text-gray-600 dark:text-gray-300">{view.title}</h3>
		<p
			class="mt-2 text-3xl font-semibold tracking-tight text-gray-900 dark:text-white"
			aria-live="polite"
		>
			{format(metric)}{#if view.unit}<span
					class="ml-1 text-sm font-normal text-gray-500 dark:text-gray-400">{view.unit}</span
				>{/if}
		</p>
		{#if view.description}<p class="mt-2 text-xs text-gray-500 dark:text-gray-400">
				{view.description}
			</p>{/if}
		{#if metric === null}<p class="mt-1 text-xs text-gray-500">
				{$i18n.t('No matching numeric data')}
			</p>{/if}
	</section>
{:else}
	<section class="min-w-0 space-y-3" data-table={view.__key}>
		<div class="flex flex-wrap items-center justify-between gap-2">
			<h3 class="text-sm font-semibold text-gray-900 dark:text-gray-100">{view.title}</h3>
			<span aria-live="polite" class="text-xs text-gray-500"
				>{$i18n.t('{{count}} row', { count: rows.length })}</span
			>
		</div>
		{#if view.searchable}<label class="block"
				><span class="sr-only">{$i18n.t('Search {{title}}', { title: view.title })}</span><input
					type="search"
					value={search}
					maxlength="160"
					placeholder={$i18n.t('Search table…')}
					class="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 focus:border-indigo-500 focus:outline-none dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100"
					oninput={(event) =>
						context.write(`search:${view.__key}`, event.currentTarget.value.slice(0, 160))}
				/></label
			>{/if}
		<div class="max-h-96 overflow-auto rounded-xl border border-gray-200 dark:border-gray-700">
			<table class="w-full text-left text-sm">
				<caption class="sr-only">{view.title}</caption><thead
					class="sticky top-0 bg-gray-50 text-xs text-gray-600 dark:bg-gray-800 dark:text-gray-300"
					><tr>
						{#if view.selectable}<th scope="col" class="p-3"
								><span class="sr-only">{$i18n.t('Select')}</span></th
							>{/if}
						{#each view.columns as column}<th
								scope="col"
								class="px-3 py-2"
								aria-sort={sort?.field === column.key
									? sort?.direction === 'asc'
										? 'ascending'
										: 'descending'
									: 'none'}
							>
								{#if view.sortable}<button
										type="button"
										class="flex items-center gap-1 rounded py-1 text-left focus-visible:outline-indigo-500"
										onclick={() => sortColumn(column.key)}
										>{column.label}<span aria-hidden="true"
											>{sort?.field === column.key
												? sort?.direction === 'asc'
													? '↑'
													: '↓'
												: '↕'}</span
										></button
									>{:else}{column.label}{/if}
							</th>{/each}
					</tr></thead
				><tbody
					>{#each rows as row, index}<tr
							class="border-t border-gray-100 text-gray-700 dark:border-gray-800 dark:text-gray-200"
							class:bg-indigo-50={view.selectable && selected === String(row[view.rowKey])}
						>
							{#if view.selectable}<td class="p-3"
									><input
										type="radio"
										name={`${view.__scope}-${view.__key}-selection`}
										aria-label={$i18n.t('Select row {{number}} in {{title}}', {
											number: index + 1,
											title: view.title
										})}
										checked={selected === String(row[view.rowKey])}
										class="accent-indigo-600"
										onchange={() =>
											context.writeShared(view.selectionKey, String(row[view.rowKey]))}
									/></td
								>{/if}
							{#each view.columns as column}<td
									class="max-w-72 whitespace-pre-wrap break-words px-3 py-2 tabular-nums"
									>{format(row[column.key])}</td
								>{/each}
						</tr>{/each}</tbody
				>
			</table>
			{#if rows.length === 0}<p class="p-5 text-center text-sm text-gray-500">
					{$i18n.t('No matching rows')}
				</p>{/if}
		</div>
		{#if view.selectable && selected !== ''}<button
				type="button"
				class="rounded text-xs font-medium text-indigo-600 focus-visible:outline-indigo-500 dark:text-indigo-400"
				onclick={() => context.writeShared(view.selectionKey, '')}
				>{$i18n.t('Clear selection')}</button
			>{/if}
	</section>
{/if}

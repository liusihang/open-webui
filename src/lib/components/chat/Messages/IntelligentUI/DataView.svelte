<script lang="ts">
	import { getContext } from 'svelte';
	import type { Readable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import { appearanceStyle, tableCellAppearance } from '$lib/utils/intelligent-ui/colors';
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
		class="iui-surface iui-metric rounded-2xl border p-5 shadow-sm"
		style={appearanceStyle(view.appearance, { surface: true })}
		data-metric={view.__key}
	>
		<h3 class="text-xs font-medium tracking-wide">{view.title}</h3>
		<p
			class={`mt-3 text-2xl font-semibold tracking-tight ${view.appearance?.foreground ? 'iui-ink' : 'iui-accent'}`}
			style={appearanceStyle(view.appearance, { surface: true })}
			aria-live="polite"
		>
			{format(metric)}{#if view.unit}<span class="ml-1 text-sm font-normal">{view.unit}</span>{/if}
		</p>
		{#if view.description}<p class="mt-3 text-xs leading-relaxed">
				{view.description}
			</p>{/if}
		{#if metric === null}<p class="mt-1 text-xs">
				{$i18n.t('No matching numeric data')}
			</p>{/if}
	</section>
{:else}
	<section
		class="iui-surface min-w-0 space-y-4 rounded-2xl border p-4 sm:p-5"
		style={appearanceStyle(view.appearance, { surface: true })}
		data-table={view.__key}
	>
		<div class="flex flex-wrap items-center justify-between gap-2">
			<h3 class="text-sm font-semibold tracking-tight">{view.title}</h3>
			<span aria-live="polite" class="text-xs"
				>{$i18n.t('{{count}} row', { count: rows.length })}</span
			>
		</div>
		{#if view.searchable}<label class="block"
				><span class="sr-only">{$i18n.t('Search {{title}}', { title: view.title })}</span><input
					type="search"
					value={search}
					maxlength="160"
					placeholder={$i18n.t('Search table…')}
					class="iui-surface w-full rounded-xl border px-3 py-2.5 text-sm"
					style={appearanceStyle(view.appearance)}
					oninput={(event) =>
						context.write(`search:${view.__key}`, event.currentTarget.value.slice(0, 160))}
				/></label
			>{/if}
		<div class="max-h-96 overflow-auto rounded-xl border" style="border-color:var(--iui-border)">
			<table class="w-full text-left text-sm">
				<caption class="sr-only">{view.title}</caption><thead class="sticky top-0 z-10 text-xs"
					><tr>
						{#if view.selectable}<th scope="col" class="p-3"
								><span class="sr-only">{$i18n.t('Select')}</span></th
							>{/if}
						{#each view.columns as column}<th
								scope="col"
								class="iui-surface px-3 py-3 font-semibold"
								style={appearanceStyle(
									{ ...view.appearance, ...column.appearance },
									{ surface: true }
								)}
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
							class="border-t"
							style="border-color:var(--iui-border)"
							aria-selected={view.selectable ? selected === String(row[view.rowKey]) : undefined}
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
										style="accent-color:var(--iui-accent)"
										onchange={() =>
											context.writeShared(view.selectionKey, String(row[view.rowKey]))}
									/></td
								>{/if}
							{#each view.columns as column}<td
									class="iui-surface max-w-72 whitespace-pre-wrap break-words px-3 py-3 tabular-nums"
									style={appearanceStyle(tableCellAppearance(view, row, column))}
									data-row-id={view.rowKey ? String(row[view.rowKey]) : undefined}
									data-column={column.key}>{format(row[column.key])}</td
								>{/each}
						</tr>{/each}</tbody
				>
			</table>
			{#if rows.length === 0}<p class="p-5 text-center text-sm">
					{$i18n.t('No matching rows')}
				</p>{/if}
		</div>
		{#if view.selectable && selected !== ''}<button
				type="button"
				class="iui-accent rounded text-xs font-medium focus-visible:outline-indigo-500"
				style={appearanceStyle(view.appearance)}
				onclick={() => context.writeShared(view.selectionKey, '')}
				>{$i18n.t('Clear selection')}</button
			>{/if}
	</section>
{/if}

<style>
	.iui-metric {
		border-top: 3px solid var(--iui-accent);
	}
	tr[aria-selected='true'] td {
		box-shadow: inset 0 -2px var(--iui-accent);
	}
</style>

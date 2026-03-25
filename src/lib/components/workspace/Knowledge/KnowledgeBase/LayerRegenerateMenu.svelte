<script context="module" lang="ts">
	import { LAYER_TYPE_ORDER, getLayerTitle, type KnowledgeLayerType } from './LayersPanel.svelte';

	export const DEFAULT_SELECTED_LAYER_TYPES: KnowledgeLayerType[] = [...LAYER_TYPE_ORDER];

	export const toggleLayerType = (
		selectedLayerTypes: KnowledgeLayerType[],
		layerType: KnowledgeLayerType
	): KnowledgeLayerType[] => {
		if (selectedLayerTypes.includes(layerType)) {
			const next = selectedLayerTypes.filter((item) => item !== layerType);
			return next.length > 0 ? next : [layerType];
		}

		return [...selectedLayerTypes, layerType].sort(
			(a, b) => LAYER_TYPE_ORDER.indexOf(a) - LAYER_TYPE_ORDER.indexOf(b)
		);
	};

	export { getLayerTitle };
</script>

<script lang="ts">
	import { getContext } from 'svelte';
	import type { KnowledgeLayerType } from './LayersPanel.svelte';

	const i18n = getContext('i18n');

	export let canManage = false;
	export let selectedLayerTypes: KnowledgeLayerType[] = [...DEFAULT_SELECTED_LAYER_TYPES];
	export let disabled = false;
	export let regenerating = false;
	export let backfilling = false;
	export let onSelectionChange: (layerTypes: KnowledgeLayerType[]) => void = () => {};
	export let onRegenerateSelected: (layerTypes: KnowledgeLayerType[]) => void | Promise<void> = async () => {};
	export let onBackfill: (layerTypes: KnowledgeLayerType[]) => void | Promise<void> = async () => {};

	const handleToggle = (layerType: KnowledgeLayerType) => {
		if (disabled) return;
		const next = toggleLayerType(selectedLayerTypes, layerType);
		onSelectionChange(next);
	};
</script>

{#if canManage}
	<section class="border-b border-gray-100 dark:border-gray-850 px-3 py-2.5 space-y-2">
		<div class="text-xs font-medium text-gray-700 dark:text-gray-200">
			{$i18n.t('Layer Actions')}
		</div>
		<div class="flex flex-wrap gap-2">
			{#each DEFAULT_SELECTED_LAYER_TYPES as layerType}
				<label class="inline-flex items-center gap-1.5 text-[11px] text-gray-600 dark:text-gray-300">
					<input
						type="checkbox"
						checked={selectedLayerTypes.includes(layerType)}
						disabled={disabled}
						on:change={() => handleToggle(layerType)}
					/>
					<span>{getLayerTitle(layerType)}</span>
				</label>
			{/each}
		</div>
		<div class="flex items-center gap-2">
			<button
				type="button"
				class="text-[11px] px-2.5 py-1 rounded-md bg-gray-100 dark:bg-gray-850 hover:bg-gray-200 dark:hover:bg-gray-800 disabled:opacity-60 disabled:cursor-not-allowed"
				disabled={disabled || backfilling}
				on:click={() => onRegenerateSelected(selectedLayerTypes)}
			>
				{#if regenerating}
					{$i18n.t('Regenerating...')}
				{:else}
					{$i18n.t('Regenerate Selected')}
				{/if}
			</button>
			<button
				type="button"
				class="text-[11px] px-2.5 py-1 rounded-md bg-gray-100 dark:bg-gray-850 hover:bg-gray-200 dark:hover:bg-gray-800 disabled:opacity-60 disabled:cursor-not-allowed"
				disabled={disabled || regenerating}
				on:click={() => onBackfill(selectedLayerTypes)}
			>
				{#if backfilling}
					{$i18n.t('Backfilling...')}
				{:else}
					{$i18n.t('Backfill Knowledge')}
				{/if}
			</button>
		</div>
	</section>
{/if}

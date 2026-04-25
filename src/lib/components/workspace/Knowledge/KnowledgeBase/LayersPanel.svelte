<script context="module" lang="ts">
	export const LAYER_TYPE_ORDER = ['abstract'] as const;
	export type KnowledgeLayerType = (typeof LAYER_TYPE_ORDER)[number];

	export type KnowledgeLayerStatus = 'pending' | 'ready' | 'failed' | 'stale';

	export type KnowledgeLayerItem = {
		layer_type: KnowledgeLayerType;
		content?: string | null;
		status?: string | null;
		updated_at?: number | null;
		part_index?: number | null;
		part_total?: number | null;
		display_title?: string | null;
	};

	export type KnowledgeLayerCard = {
		layerType: KnowledgeLayerType;
		title: string;
		content: string;
		status: KnowledgeLayerStatus;
		updatedAt: number | null;
	};

	const LAYER_TITLES: Record<KnowledgeLayerType, string> = {
		abstract: 'Abstract'
	};

	export const getLayerTitle = (layerType: KnowledgeLayerType): string =>
		LAYER_TITLES[layerType] ?? layerType;

	export const normalizeLayerStatus = (status?: string | null): KnowledgeLayerStatus => {
		if (status === 'ready' || status === 'failed' || status === 'stale') {
			return status;
		}
		return 'pending';
	};

	export const buildLayerViewModel = (layers: KnowledgeLayerItem[] = []): KnowledgeLayerCard[] => {
		const layerMap = new Map<KnowledgeLayerType, KnowledgeLayerItem[]>();
		for (const layer of layers) {
			if (!layer?.layer_type) {
				continue;
			}
			const next = layerMap.get(layer.layer_type) ?? [];
			next.push(layer);
			layerMap.set(layer.layer_type, next);
		}

		const statusPriority: Record<KnowledgeLayerStatus, number> = {
			failed: 4,
			stale: 3,
			pending: 2,
			ready: 1
		};

		return LAYER_TYPE_ORDER.map((layerType) => {
			const layerParts = (layerMap.get(layerType) ?? []).sort((a, b) => {
				const partA = a.part_index ?? 1;
				const partB = b.part_index ?? 1;
				if (partA !== partB) return partA - partB;
				return (b.updated_at ?? 0) - (a.updated_at ?? 0);
			});

			let status: KnowledgeLayerStatus = 'pending';
			if (layerParts.length > 0) {
				status = normalizeLayerStatus(layerParts[0]?.status);
				for (const part of layerParts.slice(1)) {
					const nextStatus = normalizeLayerStatus(part?.status);
					if (statusPriority[nextStatus] > statusPriority[status]) {
						status = nextStatus;
					}
				}
			}

			const content = layerParts
				.map((part) => {
					const partContent = part?.content ?? '';
					if (!partContent) return '';
					if ((part?.part_total ?? 1) > 1) {
						const partTitle = part.display_title ?? `${getLayerTitle(layerType)}`;
						return `${partTitle}: ${partContent}`;
					}
					return partContent;
				})
				.filter(Boolean)
				.join('\n\n');

			const updatedAt =
				layerParts.length > 0 ? Math.max(...layerParts.map((part) => part?.updated_at ?? 0)) : null;

			return {
				layerType,
				title: getLayerTitle(layerType),
				content,
				status,
				updatedAt: updatedAt && updatedAt > 0 ? updatedAt : null
			};
		});
	};

	export const getLayerFallbackCopy = (status: KnowledgeLayerStatus): string => {
		if (status === 'failed') {
			return 'Layer generation failed. Try regenerating this layer.';
		}
		if (status === 'stale') {
			return 'Layer content is stale. Regenerate to refresh.';
		}
		return 'Layer content is not available yet.';
	};
</script>

<script lang="ts">
	import { getContext } from 'svelte';
	import dayjs from '$lib/dayjs';
	import relativeTime from 'dayjs/plugin/relativeTime';
	import Spinner from '$lib/components/common/Spinner.svelte';

	dayjs.extend(relativeTime);

	const i18n = getContext('i18n');

	export let layers: KnowledgeLayerItem[] = [];
	export let loading = false;
	export let error: string | null = null;
	export let canManage = false;
	export let regeneratingAll = false;
	export let regeneratingLayerType: KnowledgeLayerType | null = null;
	export let onRegenerateAll: () => void | Promise<void> = async () => {};
	export let onRegenerateLayer: (
		layerType: KnowledgeLayerType
	) => void | Promise<void> = async () => {};
	export let onRetry: () => void | Promise<void> = async () => {};

	$: layerCards = buildLayerViewModel(layers);

	const STATUS_CLASSES: Record<KnowledgeLayerStatus, string> = {
		ready: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300',
		pending: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-300',
		failed: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300',
		stale: 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300'
	};

	const getStatusLabel = (status: KnowledgeLayerStatus): string => {
		if (status === 'ready') return $i18n.t('Ready');
		if (status === 'failed') return $i18n.t('Failed');
		if (status === 'stale') return $i18n.t('Stale');
		return $i18n.t('Pending');
	};

	$: isBusy = regeneratingAll || regeneratingLayerType !== null;
	$: hasLayerData = layers.length > 0;
</script>

<section
	class="border-b border-gray-100 dark:border-gray-850 px-3 py-3 space-y-3"
	role="region"
	aria-labelledby="knowledge-layers-heading"
	aria-busy={loading}
>
	<div class="flex items-start justify-between gap-2">
		<div>
			<div class="text-sm font-semibold" id="knowledge-layers-heading">{$i18n.t('Layers')}</div>
			<div class="mt-0.5 text-[11px] text-gray-500 dark:text-gray-400">
				{$i18n.t('Review summary layers before opening full text')}
			</div>
		</div>
		{#if canManage}
			<button
				type="button"
				class="shrink-0 min-h-8 text-xs px-2.5 py-1 rounded-lg bg-gray-100 dark:bg-gray-850 hover:bg-gray-200 dark:hover:bg-gray-800 disabled:opacity-60 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gray-400 dark:focus-visible:ring-gray-600"
				disabled={isBusy}
				on:click={() => {
					onRegenerateAll();
				}}
			>
				{#if regeneratingAll}
					{$i18n.t('Regenerating...')}
				{:else}
					{$i18n.t('Regenerate All')}
				{/if}
			</button>
		{/if}
	</div>

	{#if loading}
		<div
			class="rounded-lg border border-gray-100 dark:border-gray-850 bg-gray-50/60 dark:bg-gray-900/30 p-2.5 space-y-2"
			role="status"
			aria-live="polite"
		>
			<div class="text-xs text-gray-500 dark:text-gray-400 flex items-center gap-2">
				<Spinner className="size-3.5" />
				{$i18n.t('Loading layers...')}
			</div>
			<div class="space-y-1.5" aria-hidden="true">
				<div class="h-2 rounded bg-gray-200/80 dark:bg-gray-800 animate-pulse" />
				<div class="h-2 w-3/4 rounded bg-gray-200/80 dark:bg-gray-800 animate-pulse" />
			</div>
		</div>
	{:else if error}
		<div
			class="text-xs text-red-700 dark:text-red-300 bg-red-50 dark:bg-red-900/20 border border-red-100 dark:border-red-900/40 rounded-lg px-2.5 py-2 space-y-1.5"
			role="alert"
			aria-live="assertive"
		>
			<div>{$i18n.t('Failed to load layers: {{error}}', { error })}</div>
			<button
				type="button"
				class="text-[11px] px-2 py-1 rounded-md bg-white/70 dark:bg-gray-900/50 border border-red-200/70 dark:border-red-900/50 hover:bg-white dark:hover:bg-gray-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-300 dark:focus-visible:ring-red-800"
				on:click={() => {
					onRetry();
				}}
			>
				{$i18n.t('Retry')}
			</button>
		</div>
	{:else if !hasLayerData}
		<div
			class="text-xs text-gray-600 dark:text-gray-300 bg-gray-50 dark:bg-gray-900/40 border border-gray-100 dark:border-gray-850 rounded-lg px-2.5 py-2"
			role="status"
			aria-live="polite"
		>
			{$i18n.t('No layer content is available yet.')}
		</div>
	{:else}
		<div class="space-y-2.5">
			{#each layerCards as card (card.layerType)}
				<div class="rounded-lg border border-gray-100 dark:border-gray-850 p-2.5 space-y-2">
					<div class="flex items-center justify-between gap-2">
						<div class="font-medium text-xs">{card.title}</div>
						<div class="flex items-center gap-2">
							<span
								class={`px-1.5 py-0.5 rounded-full text-[10px] font-medium ${STATUS_CLASSES[card.status]}`}
								aria-label={$i18n.t('Layer status: {{status}}', {
									status: getStatusLabel(card.status)
								})}
							>
								{getStatusLabel(card.status)}
							</span>
							{#if canManage}
								<button
									type="button"
									class="text-[11px] px-1.5 py-0.5 rounded-md bg-gray-100 dark:bg-gray-850 hover:bg-gray-200 dark:hover:bg-gray-800 disabled:opacity-60 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gray-400 dark:focus-visible:ring-gray-600"
									disabled={isBusy}
									aria-label={$i18n.t('Regenerate {{title}} layer', { title: card.title })}
									on:click={() => {
										onRegenerateLayer(card.layerType);
									}}
								>
									{#if regeneratingLayerType === card.layerType}
										{$i18n.t('Regenerating...')}
									{:else}
										{$i18n.t('Regenerate')}
									{/if}
								</button>
							{/if}
						</div>
					</div>

					<div class="text-xs leading-5 whitespace-pre-wrap text-gray-700 dark:text-gray-200">
						{#if card.content}
							{card.content}
						{:else}
							<span class="text-gray-500 dark:text-gray-400">
								{getLayerFallbackCopy(card.status)}
							</span>
						{/if}
					</div>

					{#if card.updatedAt}
						<div class="text-[10px] text-gray-400 dark:text-gray-500">
							{$i18n.t('Updated {{time}}', { time: dayjs.unix(card.updatedAt).fromNow() })}
						</div>
					{/if}
				</div>
			{/each}
		</div>
	{/if}
</section>

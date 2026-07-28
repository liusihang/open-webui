<script lang="ts">
	import { getContext, tick } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import { models, settings } from '$lib/stores';
	import { updateUserSettings } from '$lib/apis/users';

	import Dropdown from '../common/Dropdown.svelte';
	import ChevronDown from '../icons/ChevronDown.svelte';
	import Selector from './ModelSelector/Selector.svelte';
	import ReasoningEffortSlider from './ReasoningEffortSlider.svelte';
	import {
		resolveModelReasoningEfforts,
		type ReasoningEffort
	} from './agentModeRequest';

	const i18n = getContext<Writable<i18nType>>('i18n');

	export let selectedModels: string[] = [''];
	export let reasoningEffort: ReasoningEffort = 'medium';
	export let disabled = false;

	let show = false;
	let triggerButton: HTMLButtonElement;
	let restoreFocusOnClose = false;

	$: selectedModelId = selectedModels[0] ?? '';
	$: selectedModel = $models.find((model) => model.id === selectedModelId);
	$: allowedEfforts = resolveModelReasoningEfforts(selectedModel);
	$: reasoningAvailable = allowedEfforts.length > 0;
	$: allEffortOptions = [
		{ value: 'low' as const, label: $i18n.t('Light reasoning') },
		{ value: 'medium' as const, label: $i18n.t('Standard reasoning') },
		{ value: 'high' as const, label: $i18n.t('Deep reasoning') },
		{ value: 'xhigh' as const, label: $i18n.t('Extra deep reasoning') }
	];
	$: effortOptions = allEffortOptions.filter((option) => allowedEfforts.includes(option.value));
	$: effortLabel = effortOptions.find((option) => option.value === reasoningEffort)?.label;
	$: triggerLabel = [
		selectedModel?.name ?? selectedModelId ?? $i18n.t('Select a model'),
		reasoningAvailable ? effortLabel : undefined
	]
		.filter(Boolean)
		.join(' · ');

	$: if (reasoningAvailable && !allowedEfforts.includes(reasoningEffort)) {
		reasoningEffort = allowedEfforts.includes('medium') ? 'medium' : allowedEfforts[0];
	}

	const pinModelHandler = async (modelId: string) => {
		const currentPinnedModels = ($settings?.pinnedModels ?? []) as string[];
		const pinnedModels = currentPinnedModels.includes(modelId)
			? currentPinnedModels.filter((id) => id !== modelId)
			: [...new Set([...currentPinnedModels, modelId])];

		settings.set({ ...$settings, pinnedModels });
		await updateUserSettings(localStorage.token, { ui: $settings });
	};

	const handleOpenChange = async (open: boolean) => {
		show = open;
		if (!open && restoreFocusOnClose) {
			restoreFocusOnClose = false;
			await tick();
			triggerButton?.focus();
		}
	};

	const handleWindowKeydown = (event: KeyboardEvent) => {
		if (show && event.key === 'Escape') {
			restoreFocusOnClose = true;
		}
	};
</script>

<svelte:window on:keydown={handleWindowKeydown} />

<Dropdown
	bind:show
	side="top"
	align="end"
	sideOffset={8}
	onOpenChange={handleOpenChange}
	contentClass="w-56 select-none rounded-2xl border border-gray-100 bg-white p-1.5 text-gray-900 shadow-lg dark:border-gray-800 dark:bg-gray-850 dark:text-gray-100"
>
	<button
		bind:this={triggerButton}
		type="button"
		disabled={disabled}
		aria-label={$i18n.t('Model and reasoning settings')}
		aria-haspopup="menu"
		aria-expanded={show}
		class="group flex h-9 max-w-[13rem] items-center gap-1.5 rounded-full px-3 text-sm text-gray-600 transition-colors duration-150 hover:bg-gray-100 hover:text-gray-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500/35 disabled:cursor-not-allowed disabled:opacity-50 dark:text-gray-300 dark:hover:bg-gray-800 dark:hover:text-gray-100"
	>
		<span class="truncate">{triggerLabel || $i18n.t('Select a model')}</span>
		<ChevronDown className="size-3 shrink-0 text-gray-400 transition-transform duration-150" />
	</button>

	<svelte:fragment slot="content">
		<div class="px-1 py-0.5">
			<div class="flex min-h-9 items-center gap-3 rounded-xl px-2 hover:bg-gray-50 dark:hover:bg-gray-800/70">
				<span class="shrink-0 text-xs font-medium text-gray-500 dark:text-gray-400">
					{$i18n.t('Model')}
				</span>
				<div class="min-w-0 flex-1 text-right">
					<Selector
						id="composer"
						placeholder={$i18n.t('Select a model')}
						items={$models.map((model) => ({
							value: model.id,
							label: model.name,
							model
						}))}
						className="w-[min(30rem,calc(100vw-2rem))]"
						triggerClassName="text-sm text-gray-700 dark:text-gray-200"
						selectionOnly={true}
						{pinModelHandler}
						bind:value={selectedModels[0]}
					/>
				</div>
			</div>

			<div class="mt-0.5 rounded-xl px-2 py-1.5">
				<div class="mb-1 flex items-center justify-between gap-3">
					<span class="text-xs font-medium text-gray-500 dark:text-gray-400">
						{$i18n.t('Reasoning Effort')}
					</span>
					{#if reasoningAvailable}
						<span class="text-xs text-gray-700 dark:text-gray-200">{effortLabel}</span>
					{/if}
				</div>

				{#if reasoningAvailable}
					<ReasoningEffortSlider
						options={effortOptions}
						bind:value={reasoningEffort}
						ariaLabel={$i18n.t('Reasoning Effort')}
					/>
				{:else}
					<p class="px-2 pb-1 text-xs leading-5 text-gray-400 dark:text-gray-500">
						{$i18n.t('This model does not support adjustable reasoning effort')}
					</p>
				{/if}
			</div>
		</div>
	</svelte:fragment>
</Dropdown>

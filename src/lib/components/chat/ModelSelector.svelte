<script lang="ts">
	import { mobile, models, settings } from '$lib/stores';
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { toast } from 'svelte-sonner';
	import Selector from './ModelSelector/Selector.svelte';

	import { updateUserSettings } from '$lib/apis/users';
	const i18n = getContext<Writable<i18nType>>('i18n');

	export let selectedModels: string[] = [''];
	export let disabled = false;

	export let showSetDefault = true;

	$: selectedModelName =
		$models.find((model) => model.id === (selectedModels[0] ?? ''))?.name ??
		selectedModels[0] ??
		'';
	$: compactSelectedModelLabel =
		selectedModelName.split('/').filter(Boolean).at(-1) ?? selectedModelName;

	const saveDefaultModel = async () => {
		const selectedModel = selectedModels[0] ?? '';
		if (!selectedModel) {
			toast.error($i18n.t('Choose a model before saving...'));
			return;
		}
		settings.set({ ...$settings, models: [selectedModel] });
		await updateUserSettings(localStorage.token, { ui: $settings });

		toast.success($i18n.t('Default model updated'));
	};

	const pinModelHandler = async (modelId: string) => {
		let pinnedModels: string[] = $settings?.pinnedModels ?? [];

		if (pinnedModels.includes(modelId)) {
			pinnedModels = pinnedModels.filter((id) => id !== modelId);
		} else {
			pinnedModels = [...new Set([...pinnedModels, modelId])];
		}

		settings.set({ ...$settings, pinnedModels: pinnedModels });
		await updateUserSettings(localStorage.token, { ui: $settings });
	};

	$: if (selectedModels.length > 0 && $models.length > 0) {
		const selectedModel = selectedModels[0] ?? '';
		const normalizedSelectedModel = $models.some((model) => model.id === selectedModel)
			? selectedModel
			: '';

		if (normalizedSelectedModel !== selectedModel) {
			selectedModels[0] = normalizedSelectedModel;
			selectedModels = selectedModels;
		}
	}
</script>

<div
	class="flex flex-col w-full items-start"
	class:pointer-events-none={disabled}
	class:opacity-50={disabled}
>
	<div class="flex w-full max-w-fit">
		<div class="overflow-hidden w-full">
			<div class="max-w-full {($settings?.highContrastMode ?? false) ? 'm-1' : 'mr-1'}">
				<Selector
					id="0"
					placeholder={$i18n.t('Select a model')}
					selectedLabel={$mobile ? compactSelectedModelLabel : null}
					items={$models.map((model) => ({
						value: model.id,
						label: model.name,
						model: model
					}))}
					{pinModelHandler}
					bind:value={selectedModels[0]}
				/>
		</div>
	</div>
</div>
</div>

{#if showSetDefault}
	<div
		class="relative text-left mt-[1px] ml-1 text-[0.7rem] text-gray-600 dark:text-gray-400 font-primary"
	>
		<button on:click={saveDefaultModel}> {$i18n.t('Set as default')}</button>
	</div>
{/if}

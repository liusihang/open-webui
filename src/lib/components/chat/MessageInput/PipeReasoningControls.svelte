<script lang="ts">
	import { getContext } from 'svelte';
	import { models, settings, user, type Model } from '$lib/stores';
	import { updateUserSettings } from '$lib/apis/users';
	import { toast } from 'svelte-sonner';
	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import DropdownMenu from '$lib/components/common/DropdownMenu.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Knobs from '$lib/components/icons/Knobs.svelte';
	import { getPipeReasoningEfforts } from '$lib/utils/pipeReasoning';
	import { resolveLocalizedModelName } from '$lib/utils/localizedContent';

	const i18n: any = getContext('i18n');
	export let selectedModels: string[] = [];
	let saving = false;
	$: available = selectedModels
		.map((id) => $models.find((model) => model.id === id))
		.filter((model): model is Model => !!model && getPipeReasoningEfforts(model).length > 0);
	$: permitted =
		$user?.role === 'admin' ||
		(($user?.permissions?.chat?.controls ?? true) && ($user?.permissions?.chat?.params ?? true));
	$: choices = $settings?.pipeReasoningEfforts ?? {};

	const select = async (modelId: string, value: string) => {
		const previous = choices;
		const next = { ...choices };
		if (value) next[modelId] = value;
		else delete next[modelId];
		settings.set({ ...$settings, pipeReasoningEfforts: next });
		saving = true;
		try {
			if (!(await updateUserSettings(localStorage.token, { ui: { pipeReasoningEfforts: next } }))) {
				throw new Error($i18n.t('Failed to save settings'));
			}
		} catch (error) {
			settings.set({ ...$settings, pipeReasoningEfforts: previous });
			toast.error(String(error));
		} finally {
			saving = false;
		}
	};
</script>

{#if permitted && available.length}
	<Dropdown align="end" visualViewportAware>
		<Tooltip content={$i18n.t('Reasoning effort')} placement="top">
			<button
				type="button"
				aria-label={$i18n.t('Reasoning effort')}
				class="mr-2 flex shrink-0 items-center text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
			>
				<Knobs className="size-4" />
			</button>
		</Tooltip>
		<div slot="content">
			<DropdownMenu className="w-60 max-w-[calc(100vw-2rem)]">
				{#each available as model (model.id)}
					<label class="block px-2 py-1.5 text-xs text-gray-500 dark:text-gray-400">
						<span class="mb-1 block truncate"
							>{resolveLocalizedModelName(model, $i18n.language)} · {$i18n.t(
								'Reasoning effort'
							)}</span
						>
						<select
							class="w-full rounded-lg bg-transparent px-2 py-1 text-gray-700 dark:text-gray-100"
							disabled={saving}
							value={choices[model.id] ?? ''}
							on:change={(event) => select(model.id, event.currentTarget.value)}
						>
							<option value="">{$i18n.t('Default')}</option>
							{#each getPipeReasoningEfforts(model) as effort}
								<option value={effort}>{effort}</option>
							{/each}
						</select>
					</label>
				{/each}
			</DropdownMenu>
		</div>
	</Dropdown>
{/if}

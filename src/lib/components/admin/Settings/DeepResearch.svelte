<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { onMount, getContext } from 'svelte';
	import { getDeepResearchConfig, setDeepResearchConfig } from '$lib/apis/configs';

	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Switch from '$lib/components/common/Switch.svelte';

	const i18n = getContext('i18n');

	export let saveHandler: () => void | Promise<void> = () => {};

	let config = null;

	const submitHandler = async () => {
		if (!config) {
			return false;
		}

		const normalizedConfig = {
			...config,
			DEERFLOW_BASE_URL: (config.DEERFLOW_BASE_URL ?? '').trim(),
			DEERFLOW_API_KEY: (config.DEERFLOW_API_KEY ?? '').trim(),
			DEERFLOW_MODEL: (config.DEERFLOW_MODEL ?? '').trim(),
			DEERFLOW_CONNECT_TIMEOUT_SECS: Math.max(
				1,
				Number(config.DEERFLOW_CONNECT_TIMEOUT_SECS ?? 10) || 10
			),
			DEERFLOW_REQUEST_TIMEOUT_SECS: Math.max(
				5,
				Number(config.DEERFLOW_REQUEST_TIMEOUT_SECS ?? 900) || 900
			)
		};

		if (normalizedConfig.ENABLE_DEEP_RESEARCH && !normalizedConfig.DEERFLOW_BASE_URL) {
			toast.error($i18n.t('Please set DeerFlow Base URL before enabling Deep Research.'));
			return false;
		}

		const res = await setDeepResearchConfig(localStorage.token, normalizedConfig);
		if (!res) {
			return false;
		}

		config = res;
		return true;
	};

	onMount(async () => {
		const res = await getDeepResearchConfig(localStorage.token);

		if (res) {
			config = res;
		}
	});
</script>

<form
	class="flex flex-col h-full justify-between space-y-3 text-sm"
	on:submit|preventDefault={async () => {
		const saved = await submitHandler();
		if (saved) {
			saveHandler();
		}
	}}
>
	<div class="space-y-3 overflow-y-scroll scrollbar-hidden h-full">
		{#if config}
			<div>
				<div class="mb-3.5">
					<div class="mt-0.5 mb-2.5 text-base font-medium">{$i18n.t('Deep Research')}</div>

					<hr class="border-gray-100/30 dark:border-gray-850/30 my-2" />

					<div class="mb-2.5">
						<div class="flex w-full justify-between">
							<div class="self-center text-xs font-medium">
								{$i18n.t('Enable Deep Research')}
							</div>

							<Switch bind:state={config.ENABLE_DEEP_RESEARCH} />
						</div>
						<div class="mt-1 text-xs text-gray-500">
							{$i18n.t('Use DeerFlow as the backend workflow for deep research requests.')}
						</div>
					</div>

					<div class="mb-2.5 flex flex-col gap-1.5 w-full">
						<div class="text-xs font-medium">{$i18n.t('DeerFlow Base URL')}</div>

						<div class="flex w-full">
							<div class="flex-1">
								<input
									class="w-full text-sm py-0.5 placeholder:text-gray-300 dark:placeholder:text-gray-700 bg-transparent outline-hidden"
									type="text"
									placeholder={$i18n.t('e.g. http://127.0.0.1:2026')}
									bind:value={config.DEERFLOW_BASE_URL}
									autocomplete="off"
								/>
							</div>
						</div>
					</div>

					<div class="mb-2.5 flex flex-col gap-1.5 w-full">
						<div class="text-xs font-medium">{$i18n.t('DeerFlow API Key')}</div>
						<SensitiveInput
							type="text"
							placeholder={$i18n.t('Optional bearer token for DeerFlow')}
							bind:value={config.DEERFLOW_API_KEY}
							autocomplete="off"
						/>
					</div>

					<div class="mb-2.5 flex flex-col gap-1.5 w-full">
						<div class="text-xs font-medium">{$i18n.t('Default DeerFlow Model')}</div>

						<div class="flex w-full">
							<div class="flex-1">
								<input
									class="w-full text-sm py-0.5 placeholder:text-gray-300 dark:placeholder:text-gray-700 bg-transparent outline-hidden"
									type="text"
									placeholder={$i18n.t('Optional, leave empty to use DeerFlow default')}
									bind:value={config.DEERFLOW_MODEL}
									autocomplete="off"
								/>
							</div>
						</div>
					</div>

					<div class="mb-2.5 flex gap-2 w-full items-center justify-between">
						<div class="text-xs font-medium">{$i18n.t('Connect Timeout (seconds)')}</div>

						<div>
							<Tooltip content={$i18n.t('Timeout for establishing DeerFlow connection')}>
								<input
									class="dark:bg-gray-900 w-fit rounded-sm px-2 p-1 text-xs bg-transparent outline-hidden text-right"
									type="number"
									min="1"
									step="1"
									bind:value={config.DEERFLOW_CONNECT_TIMEOUT_SECS}
									placeholder={$i18n.t('e.g. 10')}
									autocomplete="off"
								/>
							</Tooltip>
						</div>
					</div>

					<div class="mb-2.5 flex gap-2 w-full items-center justify-between">
						<div class="text-xs font-medium">{$i18n.t('Request Timeout (seconds)')}</div>

						<div>
							<Tooltip content={$i18n.t('Timeout for DeerFlow streaming response')}>
								<input
									class="dark:bg-gray-900 w-fit rounded-sm px-2 p-1 text-xs bg-transparent outline-hidden text-right"
									type="number"
									min="5"
									step="1"
									bind:value={config.DEERFLOW_REQUEST_TIMEOUT_SECS}
									placeholder={$i18n.t('e.g. 900')}
									autocomplete="off"
								/>
							</Tooltip>
						</div>
					</div>

					<div class="mb-2.5">
						<div class="flex w-full justify-between">
							<div class="self-center text-xs font-medium">
								{$i18n.t('Reuse DeerFlow Threads by Chat')}
							</div>

							<Switch bind:state={config.DEERFLOW_REUSE_THREADS} />
						</div>
						<div class="mt-1 text-xs text-gray-500">
							{$i18n.t(
								'When enabled, the same OpenWebUI chat can reuse a DeerFlow thread for continuity.'
							)}
						</div>
					</div>
				</div>
			</div>
		{/if}
	</div>

	<div class="flex justify-end pt-3 text-sm font-medium">
		<button
			class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
			type="submit"
		>
			{$i18n.t('Save')}
		</button>
	</div>
</form>

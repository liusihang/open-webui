<script lang="ts">
	import { createEventDispatcher, getContext, onMount } from 'svelte';
	import type { Writable } from 'svelte/store';
	import { toast } from 'svelte-sonner';

	import { getAdminConfig, updateAdminConfig } from '$lib/apis/auths';
	import Switch from '$lib/components/common/Switch.svelte';

	const dispatch = createEventDispatcher();
	const i18n = getContext<Writable<{ t: (key: string, params?: Record<string, unknown>) => string }>>(
		'i18n'
	);

	let adminConfig: Record<string, any> | null = null;

	const updateHandler = async () => {
		if (!adminConfig) {
			return;
		}

		const res = await updateAdminConfig(localStorage.token, adminConfig).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			adminConfig = res;
			dispatch('save');
		}
	};

	onMount(async () => {
		adminConfig = await getAdminConfig(localStorage.token);
	});
</script>

{#if adminConfig !== null}
	<form
		class="flex flex-col h-full justify-between space-y-3 text-sm"
		on:submit|preventDefault={updateHandler}
	>
		<div class="overflow-y-scroll scrollbar-hidden h-full pr-1.5">
			<div class="mb-3.5">
				<div class="mt-0.5 mb-2.5 text-base font-medium">{$i18n.t('Agent Memory')}</div>

				<hr class="border-gray-100/30 dark:border-gray-850/30 my-2" />

				<div class="mb-3 flex w-full justify-between items-center">
					<div class="self-center text-xs font-medium">
						{$i18n.t('Enable Agent Memory')}
					</div>
					<Switch bind:state={adminConfig.ENABLE_AGENT_MEMORY} />
				</div>

				<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
					<div>
						<div class="text-xs mb-1">{$i18n.t('Extraction Model Override')}</div>
						<input
							class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
							type="text"
							bind:value={adminConfig.AGENT_MEMORY_EXTRACTION_MODEL}
							placeholder={$i18n.t('Current task model')}
						/>
					</div>

					<div>
						<div class="text-xs mb-1">{$i18n.t('Consolidation Model Override')}</div>
						<input
							class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
							type="text"
							bind:value={adminConfig.AGENT_MEMORY_CONSOLIDATION_MODEL}
							placeholder={$i18n.t('Current task model')}
						/>
					</div>
				</div>
			</div>

			<div class="mb-3.5">
				<div class="mb-2.5 text-base font-medium">{$i18n.t('Worker Limits')}</div>

				<hr class="border-gray-100/30 dark:border-gray-850/30 my-2" />

				<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
					<div>
						<div class="text-xs mb-1">{$i18n.t('Idle Threshold Seconds')}</div>
						<input
							class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
							type="number"
							min="0"
							bind:value={adminConfig.AGENT_MEMORY_IDLE_THRESHOLD_SECONDS}
						/>
					</div>

					<div>
						<div class="text-xs mb-1">{$i18n.t('Startup Claim Limit')}</div>
						<input
							class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
							type="number"
							min="0"
							bind:value={adminConfig.AGENT_MEMORY_STARTUP_CLAIM_LIMIT}
						/>
					</div>

					<div>
						<div class="text-xs mb-1">{$i18n.t('Extraction Claim Limit')}</div>
						<input
							class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
							type="number"
							min="0"
							bind:value={adminConfig.AGENT_MEMORY_EXTRACTION_CLAIM_LIMIT}
						/>
					</div>

					<div>
						<div class="text-xs mb-1">{$i18n.t('Consolidation Claim Limit')}</div>
						<input
							class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
							type="number"
							min="0"
							bind:value={adminConfig.AGENT_MEMORY_CONSOLIDATION_CLAIM_LIMIT}
						/>
					</div>

					<div>
						<div class="text-xs mb-1">{$i18n.t('Lease Seconds')}</div>
						<input
							class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
							type="number"
							min="0"
							bind:value={adminConfig.AGENT_MEMORY_LEASE_SECONDS}
						/>
					</div>

					<div>
						<div class="text-xs mb-1">{$i18n.t('Retry Backoff Seconds')}</div>
						<input
							class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
							type="number"
							min="0"
							bind:value={adminConfig.AGENT_MEMORY_RETRY_BACKOFF_SECONDS}
						/>
					</div>

					<div>
						<div class="text-xs mb-1">{$i18n.t('Summary Token Budget')}</div>
						<input
							class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
							type="number"
							min="0"
							bind:value={adminConfig.AGENT_MEMORY_SUMMARY_TOKEN_BUDGET}
						/>
					</div>
				</div>
			</div>

			<div class="mb-3.5">
				<div class="mb-2.5 text-base font-medium">{$i18n.t('Operations')}</div>

				<hr class="border-gray-100/30 dark:border-gray-850/30 my-2" />

				<div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
					<button
						class="text-xs px-3 py-2 bg-gray-50 dark:bg-gray-850 text-gray-400 rounded-lg font-medium cursor-not-allowed"
						type="button"
						disabled
					>
						{$i18n.t('Run Extraction')}
					</button>
					<button
						class="text-xs px-3 py-2 bg-gray-50 dark:bg-gray-850 text-gray-400 rounded-lg font-medium cursor-not-allowed"
						type="button"
						disabled
					>
						{$i18n.t('Run Consolidation')}
					</button>
					<button
						class="text-xs px-3 py-2 bg-gray-50 dark:bg-gray-850 text-gray-400 rounded-lg font-medium cursor-not-allowed"
						type="button"
						disabled
					>
						{$i18n.t('Rebuild Index')}
					</button>
					<button
						class="text-xs px-3 py-2 bg-gray-50 dark:bg-gray-850 text-gray-400 rounded-lg font-medium cursor-not-allowed"
						type="button"
						disabled
					>
						{$i18n.t('Clear Agent Memory')}
					</button>
				</div>

				<div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-3">
					<div>
						<div class="text-xs mb-1">{$i18n.t('Failed Extractions')}</div>
						<input
							class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-500 dark:bg-gray-850 outline-hidden"
							type="text"
							value="--"
							disabled
						/>
					</div>

					<div>
						<div class="text-xs mb-1">{$i18n.t('Failed Consolidations')}</div>
						<input
							class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-500 dark:bg-gray-850 outline-hidden"
							type="text"
							value="--"
							disabled
						/>
					</div>
				</div>
			</div>
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
{/if}

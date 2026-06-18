<script lang="ts">
	import { createEventDispatcher, getContext, onMount } from 'svelte';
	import type { Writable } from 'svelte/store';
	import { toast } from 'svelte-sonner';

	import {
		clearAgentMemory,
		getAgentMemoryFailedJobs,
		rebuildAgentMemoryIndex,
		retryFailedAgentMemoryJobs,
		runAgentMemoryConsolidation,
		runAgentMemoryExtraction
	} from '$lib/apis/agent-memory';
	import { getAdminConfig, updateAdminConfig } from '$lib/apis/auths';
	import Switch from '$lib/components/common/Switch.svelte';

	const dispatch = createEventDispatcher();
	const i18n = getContext<Writable<{ t: (key: string, params?: Record<string, unknown>) => string }>>(
		'i18n'
	);

	let adminConfig: Record<string, any> | null = null;
	let failedJobs: Record<string, any> | null = null;
	let failedExtractionJobs: Array<Record<string, any>> = [];
	let failedConsolidationJobs: Array<Record<string, any>> = [];
	let operationUserId = '';
	let operationScope: 'all' | 'global' | 'folder' = 'all';
	let operationFolderId = '';
	let noteMode: 'convert' | 'delete' = 'convert';
	let operationRunning = '';

	$: failedExtractionJobs = failedJobs?.extraction_jobs ?? [];
	$: failedConsolidationJobs = failedJobs?.consolidation_jobs ?? [];

	const scopeType = () => (operationScope === 'all' ? null : operationScope);
	const scopeId = () => (operationScope === 'folder' ? operationFolderId.trim() : '');
	const formatJobValue = (value: unknown) => (value === null || value === undefined || value === '' ? '-' : `${value}`);

	const validateOperationScope = () => {
		if (operationScope === 'folder' && !operationFolderId.trim()) {
			toast.error($i18n.t('Folder ID is required'));
			return false;
		}
		return true;
	};

	const loadFailedJobs = async () => {
		failedJobs = await getAgentMemoryFailedJobs(localStorage.token, operationUserId).catch((error) => {
			toast.error(`${error}`);
			return failedJobs;
		});
	};

	const runOperation = async (name: string, operation: () => Promise<any>, success: string) => {
		operationRunning = name;
		const res = await operation().catch((error) => {
			toast.error(`${error}`);
			return null;
		});
		operationRunning = '';

		if (res) {
			toast.success($i18n.t(success));
			await loadFailedJobs();
		}
		return res;
	};

	const runExtractionHandler = async () => {
		await runOperation(
			'extraction',
			() => runAgentMemoryExtraction(localStorage.token, null),
			'Extraction run completed'
		);
	};

	const runConsolidationHandler = async () => {
		await runOperation(
			'consolidation',
			() => runAgentMemoryConsolidation(localStorage.token, null),
			'Consolidation run completed'
		);
	};

	const rebuildIndexHandler = async () => {
		if (!operationUserId.trim()) {
			toast.error($i18n.t('User ID is required'));
			return;
		}
		if (!validateOperationScope()) {
			return;
		}
		await runOperation(
			'rebuild',
			() =>
				rebuildAgentMemoryIndex(
					localStorage.token,
					operationUserId,
					scopeType(),
					scopeId()
				),
			'Index rebuild completed'
		);
	};

	const clearMemoryHandler = async () => {
		if (!operationUserId.trim()) {
			toast.error($i18n.t('User ID is required'));
			return;
		}
		if (!validateOperationScope()) {
			return;
		}
		if (!confirm($i18n.t('Clear Agent Memory?'))) {
			return;
		}
		await runOperation(
			'clear',
			() =>
				clearAgentMemory(
					localStorage.token,
					operationUserId,
					noteMode,
					scopeType(),
					scopeId()
				),
			'Agent Memory cleared'
		);
	};

	const retryFailedHandler = async () => {
		await runOperation(
			'retry',
			() => retryFailedAgentMemoryJobs(localStorage.token, operationUserId),
			'Failed jobs requeued'
		);
	};

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
		await loadFailedJobs();
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

				<div class="grid grid-cols-1 md:grid-cols-2 gap-3 mb-3">
					<div>
						<div class="text-xs mb-1">{$i18n.t('User ID')}</div>
						<input
							class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
							type="text"
							bind:value={operationUserId}
							on:change={loadFailedJobs}
						/>
					</div>

					<div>
						<div class="text-xs mb-1">{$i18n.t('Scope')}</div>
						<select
							class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
							bind:value={operationScope}
						>
							<option value="all">{$i18n.t('All')}</option>
							<option value="global">{$i18n.t('Global')}</option>
							<option value="folder">{$i18n.t('Folder')}</option>
						</select>
					</div>

					{#if operationScope === 'folder'}
						<div>
							<div class="text-xs mb-1">{$i18n.t('Folder ID')}</div>
							<input
								class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
								type="text"
								bind:value={operationFolderId}
							/>
						</div>
					{/if}

					<div>
						<div class="text-xs mb-1">{$i18n.t('Linked Notes')}</div>
						<select
							class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
							bind:value={noteMode}
						>
							<option value="convert">{$i18n.t('Convert')}</option>
							<option value="delete">{$i18n.t('Delete')}</option>
						</select>
					</div>
				</div>

				<div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
					<button
						class="text-xs px-3 py-2 bg-gray-50 hover:bg-gray-100 dark:bg-gray-850 dark:hover:bg-gray-800 rounded-lg font-medium transition disabled:opacity-50"
						type="button"
						disabled={operationRunning !== ''}
						on:click={runExtractionHandler}
					>
						{$i18n.t('Run Extraction')}
					</button>
					<button
						class="text-xs px-3 py-2 bg-gray-50 hover:bg-gray-100 dark:bg-gray-850 dark:hover:bg-gray-800 rounded-lg font-medium transition disabled:opacity-50"
						type="button"
						disabled={operationRunning !== ''}
						on:click={runConsolidationHandler}
					>
						{$i18n.t('Run Consolidation')}
					</button>
					<button
						class="text-xs px-3 py-2 bg-gray-50 hover:bg-gray-100 dark:bg-gray-850 dark:hover:bg-gray-800 rounded-lg font-medium transition disabled:opacity-50"
						type="button"
						disabled={operationRunning !== ''}
						on:click={rebuildIndexHandler}
					>
						{$i18n.t('Rebuild Index')}
					</button>
					<button
						class="text-xs px-3 py-2 bg-gray-50 hover:bg-gray-100 dark:bg-gray-850 dark:hover:bg-gray-800 rounded-lg font-medium transition disabled:opacity-50"
						type="button"
						disabled={operationRunning !== ''}
						on:click={clearMemoryHandler}
					>
						{$i18n.t('Clear Agent Memory')}
					</button>
					<button
						class="text-xs px-3 py-2 bg-gray-50 hover:bg-gray-100 dark:bg-gray-850 dark:hover:bg-gray-800 rounded-lg font-medium transition disabled:opacity-50 sm:col-span-2"
						type="button"
						disabled={operationRunning !== ''}
						on:click={retryFailedHandler}
					>
						{$i18n.t('Retry Failed Jobs')}
					</button>
				</div>

				<div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-3">
					<div>
						<div class="text-xs mb-1">{$i18n.t('Failed Extractions')}</div>
						<input
							class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-500 dark:bg-gray-850 outline-hidden"
							type="text"
							value={failedJobs?.extraction_jobs_failed ?? 0}
							disabled
						/>
					</div>

					<div>
						<div class="text-xs mb-1">{$i18n.t('Failed Consolidations')}</div>
						<input
							class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-500 dark:bg-gray-850 outline-hidden"
							type="text"
							value={failedJobs?.consolidation_jobs_failed ?? 0}
							disabled
						/>
					</div>
				</div>

				<div class="mt-4">
					<div class="mb-2.5 text-base font-medium">{$i18n.t('Inspect Failed Jobs')}</div>

					<hr class="border-gray-100/30 dark:border-gray-850/30 my-2" />

					<div class="grid grid-cols-1 xl:grid-cols-2 gap-3 mt-3">
						<section class="space-y-2">
							<div class="flex items-center justify-between gap-2">
								<div class="text-xs font-medium">{$i18n.t('Failed Extractions')}</div>
								<div class="text-[11px] text-gray-500 dark:text-gray-400">
									{failedExtractionJobs.length}
								</div>
							</div>

							{#if failedExtractionJobs.length > 0}
								<div class="space-y-2">
									{#each failedExtractionJobs as job}
										<div class="rounded-lg border border-gray-100/60 dark:border-gray-850/60 bg-gray-50/70 dark:bg-gray-850/40 p-3">
											<div class="flex items-start justify-between gap-3">
												<div class="min-w-0">
													<div class="text-[11px] font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
														{$i18n.t('Chat ID')}
													</div>
													<div class="break-all text-xs font-medium text-gray-900 dark:text-gray-100">
														{job.chat_id}
													</div>
												</div>
												<div class="shrink-0 text-[11px] font-medium capitalize text-gray-600 dark:text-gray-300">
													{job.status}
												</div>
											</div>

											<div class="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-x-3 gap-y-2 text-[11px] text-gray-600 dark:text-gray-400">
												<div>
													<span class="font-medium text-gray-700 dark:text-gray-200">{$i18n.t('User ID')}:</span>
													<span class="break-all">{job.user_id}</span>
												</div>
												<div>
													<span class="font-medium text-gray-700 dark:text-gray-200">{$i18n.t('Retry Count')}:</span>
													<span>{job.retry_count}</span>
												</div>
												<div>
													<span class="font-medium text-gray-700 dark:text-gray-200">{$i18n.t('Updated At')}:</span>
													<span>{formatJobValue(job.updated_at)}</span>
												</div>
												<div class="sm:col-span-2">
													<span class="font-medium text-gray-700 dark:text-gray-200">{$i18n.t('Last Error')}:</span>
													<span class="break-all">{formatJobValue(job.last_error)}</span>
												</div>
											</div>
										</div>
									{/each}
								</div>
							{:else}
								<div class="rounded-lg border border-dashed border-gray-200 dark:border-gray-800 px-3 py-3 text-xs text-gray-500 dark:text-gray-400">
									{$i18n.t('No failed extraction jobs to inspect')}
								</div>
							{/if}
						</section>

						<section class="space-y-2">
							<div class="flex items-center justify-between gap-2">
								<div class="text-xs font-medium">{$i18n.t('Failed Consolidations')}</div>
								<div class="text-[11px] text-gray-500 dark:text-gray-400">
									{failedConsolidationJobs.length}
								</div>
							</div>

							{#if failedConsolidationJobs.length > 0}
								<div class="space-y-2">
									{#each failedConsolidationJobs as job}
										<div class="rounded-lg border border-gray-100/60 dark:border-gray-850/60 bg-gray-50/70 dark:bg-gray-850/40 p-3">
											<div class="flex items-start justify-between gap-3">
												<div class="min-w-0">
													<div class="text-[11px] font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
														{$i18n.t('Scope')}
													</div>
													<div class="break-all text-xs font-medium text-gray-900 dark:text-gray-100">
														{job.scope_type}/{job.scope_id}
													</div>
												</div>
												<div class="shrink-0 text-[11px] font-medium capitalize text-gray-600 dark:text-gray-300">
													{job.status}
												</div>
											</div>

											<div class="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-x-3 gap-y-2 text-[11px] text-gray-600 dark:text-gray-400">
												<div>
													<span class="font-medium text-gray-700 dark:text-gray-200">{$i18n.t('User ID')}:</span>
													<span class="break-all">{job.user_id}</span>
												</div>
												<div>
													<span class="font-medium text-gray-700 dark:text-gray-200">{$i18n.t('Retry Count')}:</span>
													<span>{job.retry_count}</span>
												</div>
												<div>
													<span class="font-medium text-gray-700 dark:text-gray-200">{$i18n.t('Scope Type')}:</span>
													<span class="break-all">{job.scope_type}</span>
												</div>
												<div>
													<span class="font-medium text-gray-700 dark:text-gray-200">{$i18n.t('Scope ID')}:</span>
													<span class="break-all">{job.scope_id}</span>
												</div>
												<div>
													<span class="font-medium text-gray-700 dark:text-gray-200">{$i18n.t('Input Hash')}:</span>
													<span class="break-all">{formatJobValue(job.input_hash)}</span>
												</div>
												<div>
													<span class="font-medium text-gray-700 dark:text-gray-200">{$i18n.t('Updated At')}:</span>
													<span>{formatJobValue(job.updated_at)}</span>
												</div>
												<div class="sm:col-span-2">
													<span class="font-medium text-gray-700 dark:text-gray-200">{$i18n.t('Last Error')}:</span>
													<span class="break-all">{formatJobValue(job.last_error)}</span>
												</div>
											</div>
										</div>
									{/each}
								</div>
							{:else}
								<div class="rounded-lg border border-dashed border-gray-200 dark:border-gray-800 px-3 py-3 text-xs text-gray-500 dark:text-gray-400">
									{$i18n.t('No failed consolidation jobs to inspect')}
								</div>
							{/if}
						</section>
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

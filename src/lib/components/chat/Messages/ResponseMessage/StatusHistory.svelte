<script>
	import { getContext } from 'svelte';
	const i18n = getContext('i18n');

	import ChevronUp from '$lib/components/icons/ChevronUp.svelte';
	import StatusItem from './StatusHistory/StatusItem.svelte';
	import equal from 'fast-deep-equal';
	export let statusHistory = [];
	export let expand = false;

	let showHistory = false;
	let history = [];
	let visibleHistory = [];
	let currentStatusIndex = -1;
	let currentStatus = null;
	let historyStatuses = [];
	let canExpand = false;
	let currentStatusIsRunning = false;

	$: if (expand) {
		showHistory = true;
	}

	$: if (!equal(statusHistory, history)) {
		history = statusHistory ?? [];
	}

	$: visibleHistory = (history ?? []).filter((item) => item?.hidden !== true);
	$: currentStatusIndex = (() => {
		for (let idx = visibleHistory.length - 1; idx >= 0; idx -= 1) {
			if (visibleHistory[idx]?.done === false) {
				return idx;
			}
		}

		return visibleHistory.length > 0 ? visibleHistory.length - 1 : -1;
	})();
	$: currentStatus = currentStatusIndex >= 0 ? visibleHistory[currentStatusIndex] : null;
	$: historyStatuses =
		currentStatusIndex >= 0 ? visibleHistory.filter((_, idx) => idx !== currentStatusIndex) : [];
	$: canExpand = historyStatuses.length > 0;
	$: currentStatusIsRunning = currentStatus?.done === false;
	$: currentStatusText = currentStatus?.description ?? '';
</script>

{#if currentStatus}
	<div class="w-full text-xs text-gray-500 dark:text-gray-400">
		{#if canExpand}
			<details bind:open={showHistory} class="w-full">
				<summary
					class="flex list-none items-start gap-2 py-0.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gray-300/70 dark:focus-visible:ring-gray-700/70"
					aria-label={$i18n.t('Toggle status history')}
				>
					<span class="mt-0.5 flex shrink-0 items-center justify-center">
						{#if currentStatusIsRunning}
							<span class="relative flex size-2 items-center justify-center">
								<span class="absolute inline-flex size-2 animate-ping rounded-full bg-gray-400/35"></span>
								<span class="relative inline-flex size-1.5 rounded-full bg-gray-500 dark:bg-gray-400"></span>
							</span>
						{:else}
							<span class="inline-flex size-1.5 rounded-full bg-gray-400 dark:bg-gray-500"></span>
						{/if}
					</span>

					<div class="min-w-0 flex-1">
						<div
							class="{currentStatusIsRunning ? 'shimmer' : ''} line-clamp-1 text-wrap text-gray-500 dark:text-gray-400"
						>
							{currentStatusText}
						</div>
					</div>

					<ChevronUp
						className="mt-1 size-3 shrink-0 text-gray-400 transition-transform {showHistory ? '' : 'rotate-180'}"
						strokeWidth="3"
					/>
				</summary>

				{#if showHistory}
					<div class="mt-1.5 pl-3">
						{#each historyStatuses as status, idx}
							<div class="flex items-stretch gap-2 py-0.5">
								<div class="w-[13px] shrink-0">
									<div class="pt-3 px-1 mb-1.5">
										<span class="relative flex size-1.5 items-center justify-center rounded-full">
											<span class="relative inline-flex size-1.5 rounded-full bg-gray-400 dark:bg-gray-500"></span>
										</span>
									</div>
									{#if idx !== historyStatuses.length - 1}
										<div class="w-px ml-[6.5px] h-[calc(100%-14px)] bg-gray-300 dark:bg-gray-700"></div>
									{/if}
								</div>

								<div class="min-w-0 flex-1">
									<StatusItem {status} done={status?.done !== false} />
								</div>
							</div>
						{/each}
					</div>
				{/if}
			</details>
		{:else}
			<div class="flex items-start gap-2 py-0.5 text-left">
				<span class="mt-0.5 flex shrink-0 items-center justify-center">
					{#if currentStatusIsRunning}
						<span class="relative flex size-2 items-center justify-center">
							<span class="absolute inline-flex size-2 animate-ping rounded-full bg-gray-400/35"></span>
							<span class="relative inline-flex size-1.5 rounded-full bg-gray-500 dark:bg-gray-400"></span>
						</span>
					{:else}
						<span class="inline-flex size-1.5 rounded-full bg-gray-400 dark:bg-gray-500"></span>
					{/if}
				</span>

				<div class="min-w-0 flex-1">
					<div
						class="{currentStatusIsRunning ? 'shimmer' : ''} line-clamp-1 text-wrap text-gray-500 dark:text-gray-400"
					>
						{currentStatusText}
					</div>
				</div>
			</div>
		{/if}
	</div>
{/if}

<style>
	summary::-webkit-details-marker {
		display: none;
	}

	summary::marker {
		content: '';
	}
</style>

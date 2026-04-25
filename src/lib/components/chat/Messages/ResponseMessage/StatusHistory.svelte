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
</script>

{#if currentStatus}
	<div class="w-full text-sm">
		<button
			type="button"
			class="w-full min-h-11 rounded-xl border px-2.5 py-2 text-left transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-400/60 {canExpand
				? 'cursor-pointer'
				: 'cursor-default'} {currentStatusIsRunning
				? 'border-violet-300/70 bg-violet-50/60 dark:border-violet-700/60 dark:bg-violet-900/10'
				: 'border-gray-200/70 bg-gray-50/70 dark:border-gray-800/70 dark:bg-gray-900/30'}"
			aria-label={$i18n.t('Toggle status history')}
			aria-expanded={showHistory}
			aria-disabled={!canExpand}
			on:click={() => {
				if (canExpand) {
					showHistory = !showHistory;
				}
			}}
		>
			<div class="flex items-start gap-2">
				<div class="min-w-0 flex-1">
					<StatusItem status={currentStatus} />
				</div>
				{#if canExpand}
					<div class="mt-1 shrink-0 text-gray-500 dark:text-gray-400">
						<ChevronUp
							className="size-3.5 transition-transform {showHistory ? '' : 'rotate-180'}"
							strokeWidth="3"
						/>
					</div>
				{/if}
			</div>
		</button>

		{#if showHistory && canExpand}
			<div class="mt-2">
				{#each historyStatuses as status, idx}
					<div class="flex items-stretch gap-2 mb-1">
						<div class="w-[13px] shrink-0">
							<div class="pt-3 px-1 mb-1.5">
								<span class="relative flex size-1.5 rounded-full justify-center items-center">
									<span
										class="relative inline-flex size-1.5 rounded-full bg-gray-500 dark:bg-gray-400"
									></span>
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
	</div>
{/if}

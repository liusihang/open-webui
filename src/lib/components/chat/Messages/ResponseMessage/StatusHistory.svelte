<script>
	import { getContext } from 'svelte';
	const i18n = getContext('i18n');

	import ChevronUp from '$lib/components/icons/ChevronUp.svelte';
	import StatusItem from './StatusHistory/StatusItem.svelte';

	export let statusHistory = [];
	export let expand = false;

	let showHistory = false;
	let history = [];
	let visibleHistory = [];
	let rawLatestStatus = null;
	let latestStatus = null;
	let canExpand = false;
	let latestStatusIsRunning = false;

	$: if (expand) {
		showHistory = true;
	} else {
		showHistory = false;
	}

	$: if (
		(statusHistory ?? []).length !== history.length ||
		JSON.stringify(statusHistory ?? []) !== JSON.stringify(history)
	) {
		history = statusHistory ?? [];
	}

	$: visibleHistory = (history ?? []).filter((item) => item?.hidden !== true);
	$: rawLatestStatus = history.length > 0 ? history.at(-1) : null;
	$: latestStatus = visibleHistory.length > 0 ? visibleHistory.at(-1) : null;
	$: canExpand = visibleHistory.length > 1;
	$: latestStatusIsRunning = latestStatus?.done === false;
</script>

{#if latestStatus && rawLatestStatus?.hidden !== true}
	<div class="w-full text-sm">
		<button
			type="button"
			class="w-full min-h-11 rounded-xl border px-2.5 py-2 text-left transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-400/60 {canExpand
				? 'cursor-pointer'
				: 'cursor-default'} {latestStatusIsRunning
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
					<StatusItem status={latestStatus} />
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
			{@const historyWithoutLatest = visibleHistory.slice(0, -1)}
			<div class="mt-2">
				{#each historyWithoutLatest as status, idx}
					<div class="flex items-stretch gap-2 mb-1">
						<div class="w-[13px] shrink-0">
							<div class="pt-3 px-1 mb-1.5">
								<span class="relative flex size-1.5 rounded-full justify-center items-center">
									<span
										class="relative inline-flex size-1.5 rounded-full bg-gray-500 dark:bg-gray-400"
									></span>
								</span>
							</div>
							{#if idx !== historyWithoutLatest.length - 1}
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

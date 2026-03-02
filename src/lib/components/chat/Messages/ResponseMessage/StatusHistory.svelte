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
			class="w-full rounded-xl border px-2.5 py-2 text-left transition {latestStatusIsRunning
				? 'border-violet-300/70 bg-violet-50/60 dark:border-violet-700/60 dark:bg-violet-900/10'
				: 'border-gray-200/70 bg-gray-50/70 dark:border-gray-800/70 dark:bg-gray-900/30'}"
			aria-label={$i18n.t('Toggle status history')}
			aria-expanded={showHistory}
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
			<div class="mt-2 px-1">
				{#each historyWithoutLatest as status, idx}
					<div class="relative pl-5 pb-2">
						<span
							class="absolute left-[5px] top-[9px] size-1.5 rounded-full bg-gray-400 dark:bg-gray-500"
						></span>
						{#if idx !== historyWithoutLatest.length - 1}
							<span
								class="absolute left-[7px] top-[14px] h-[calc(100%-2px)] w-px bg-gray-200 dark:bg-gray-700"
							></span>
						{/if}

						<StatusItem {status} done={true} />
					</div>
				{/each}
			</div>
		{/if}
	</div>
{/if}

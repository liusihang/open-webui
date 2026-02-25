<script lang="ts">
	import { slide } from 'svelte/transition';
	import { quintOut } from 'svelte/easing';

	import Markdown from '../chat/Messages/Markdown.svelte';
	import ChevronDown from '../icons/ChevronDown.svelte';
	import ChevronUp from '../icons/ChevronUp.svelte';
	import CheckCircle from '../icons/CheckCircle.svelte';
	import LightBulb from '../icons/LightBulb.svelte';
	import Spinner from './Spinner.svelte';

	type SequentialThinkingEntry = {
		callId?: string;
		toolName?: string;
		thoughtNumber?: number | null;
		totalThoughts?: number | null;
		thought?: string;
		nextThoughtNeeded?: boolean | null;
		branchId?: string | null;
		branchFromThought?: number | null;
		isRevision?: boolean;
		revisesThought?: number | null;
		thoughtHistoryLength?: number | null;
		rawArguments?: string;
		rawResult?: string;
		parsedArguments?: unknown;
		parsedResult?: unknown;
	};

	export let id: string = '';
	export let entries: SequentialThinkingEntry[] = [];
	export let className = '';

	let open = false;
	let showRaw = false;
	let initialized = false;
	const fallbackId = `sequential-thinking-${Math.random().toString(36).slice(2, 10)}`;

	$: componentId = id || fallbackId;
	$: firstEntry = entries?.[0];
	$: lastEntry = entries?.[entries.length - 1];
	$: stepCount = entries?.length ?? 0;
	$: isInProgress = Boolean(lastEntry?.nextThoughtNeeded);
	$: progressPercent =
		typeof lastEntry?.thoughtNumber === 'number' && typeof lastEntry?.totalThoughts === 'number'
			? Math.min(100, Math.max(0, (lastEntry.thoughtNumber / lastEntry.totalThoughts) * 100))
			: 0;

	$: if (!initialized) {
		open = isInProgress;
		initialized = true;
	}

	function stringifyJson(value: unknown): string {
		if (value === null || value === undefined || value === '') {
			return '{}';
		}

		if (typeof value === 'string') {
			const trimmed = value.trim();
			if (!trimmed) {
				return '{}';
			}
			try {
				return JSON.stringify(JSON.parse(trimmed), null, 2);
			} catch {
				return JSON.stringify(trimmed, null, 2);
			}
		}

		try {
			return JSON.stringify(value, null, 2);
		} catch {
			return String(value);
		}
	}

	function getRawPayloadMarkdown(entry: SequentialThinkingEntry, idx: number): string {
		const stepLabel =
			typeof entry.thoughtNumber === 'number'
				? `Step ${entry.thoughtNumber}`
				: `Step ${idx + 1}`;
		const args = stringifyJson(entry.parsedArguments ?? entry.rawArguments);
		const result = stringifyJson(entry.parsedResult ?? entry.rawResult);

		return `### ${stepLabel}
Arguments
\`\`\`json
${args}
\`\`\`

Result
\`\`\`json
${result}
\`\`\``;
	}
</script>

<div {id} class={className}>
	<div
		class="w-full overflow-hidden rounded-xl border {isInProgress
			? 'border-sky-300/70 dark:border-sky-700/60'
			: 'border-gray-200/70 dark:border-gray-800/70'}"
	>
		<button
			type="button"
			class="w-full cursor-pointer px-3 py-2 text-left font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/70 {isInProgress
				? 'bg-sky-50/70 text-sky-900 dark:bg-sky-900/15 dark:text-sky-100'
				: 'bg-gray-50/70 text-gray-700 dark:bg-gray-900/30 dark:text-gray-200'}"
			on:click={() => {
				open = !open;
			}}
		>
			<div class="flex items-center justify-between gap-2">
				<div class="min-w-0 flex items-center gap-2">
					<div
						class="rounded-md p-1 {isInProgress
							? 'bg-sky-100/80 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300'
							: 'bg-amber-100/80 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300'}"
					>
						{#if isInProgress}
							<Spinner className="size-3.5" />
						{:else}
							<LightBulb className="size-3.5" />
						{/if}
					</div>

					<div class="min-w-0">
						<div class="line-clamp-1 text-base leading-6 {isInProgress ? 'shimmer' : ''}">
							Sequential Thinking
						</div>
						<div class="line-clamp-1 text-xs text-gray-500 dark:text-gray-400">
							{#if typeof lastEntry?.thoughtNumber === 'number' && typeof lastEntry?.totalThoughts === 'number'}
								{stepCount} steps · Step {lastEntry.thoughtNumber}/{lastEntry.totalThoughts}
							{:else}
								{stepCount} steps
							{/if}
						</div>
					</div>
				</div>

				<div class="flex items-center gap-1">
					<div class="text-xs text-gray-500 dark:text-gray-400">
						{isInProgress ? 'Thinking...' : 'Completed'}
					</div>
					{#if isInProgress}
						<Spinner className="size-3.5" />
					{:else}
						<CheckCircle className="size-3.5 text-emerald-500" />
					{/if}
					{#if open}
						<ChevronUp strokeWidth="3.5" className="size-3.5" />
					{:else}
						<ChevronDown strokeWidth="3.5" className="size-3.5" />
					{/if}
				</div>
			</div>
		</button>

		{#if open}
			<div
				class="border-t border-gray-200/70 bg-gray-50/70 px-3 py-2 dark:border-gray-800/70 dark:bg-gray-900/30"
				transition:slide={{ duration: 300, easing: quintOut, axis: 'y' }}
			>
				{#if progressPercent > 0}
					<div class="mb-2">
						<div class="h-1.5 w-full overflow-hidden rounded-full bg-gray-200/70 dark:bg-gray-800/70">
							<div
								class="h-full rounded-full bg-sky-500/80 transition-all duration-300"
								style={`width: ${progressPercent}%`}
							></div>
						</div>
					</div>
				{/if}

				<div class="space-y-2">
					{#each entries as entry, idx (`${entry.callId ?? idx}`)}
						<div class="relative pl-5">
							<span
								class="absolute left-[5px] top-[9px] size-1.5 rounded-full bg-gray-400 dark:bg-gray-500"
							></span>
							{#if idx !== entries.length - 1}
								<span
									class="absolute left-[7px] top-[14px] h-[calc(100%-2px)] w-px bg-gray-200 dark:bg-gray-700"
								></span>
							{/if}

							<div
								class="rounded-lg border border-gray-200/70 bg-gray-50/70 px-2.5 py-2 dark:border-gray-800/70 dark:bg-gray-900/35"
							>
								<div class="flex items-center justify-between gap-2">
									<div class="text-sm font-medium text-gray-700 dark:text-gray-200">
										{#if typeof entry.thoughtNumber === 'number' && typeof entry.totalThoughts === 'number'}
											Step {entry.thoughtNumber}/{entry.totalThoughts}
										{:else}
											Step {idx + 1}
										{/if}
									</div>

									{#if entry.branchId}
										<div
											class="rounded-md bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-600 dark:bg-gray-850 dark:text-gray-300"
										>
											{entry.branchId}
										</div>
									{/if}
								</div>

								{#if entry.isRevision && typeof entry.revisesThought === 'number'}
									<div class="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
										Revision of step {entry.revisesThought}
									</div>
								{:else if typeof entry.branchFromThought === 'number'}
									<div class="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
										Branch from step {entry.branchFromThought}
									</div>
								{/if}

								{#if entry.thought}
									<div class="mt-1 sequential-thinking-markdown text-sm leading-6 text-gray-600 dark:text-gray-300">
										<Markdown
											id={`${componentId}-sequential-thinking-${idx}`}
											content={entry.thought}
											done={true}
											editCodeBlock={false}
										/>
									</div>
								{/if}
							</div>
						</div>
					{/each}
				</div>

				<div class="mt-2 flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
					{#if typeof lastEntry?.thoughtHistoryLength === 'number'}
						<span>History: {lastEntry.thoughtHistoryLength}</span>
					{/if}
					<span>
						nextThoughtNeeded: {lastEntry?.nextThoughtNeeded ? 'true' : 'false'}
					</span>
				</div>

				<div class="mt-2">
					<button
						type="button"
						class="text-xs text-gray-500 underline-offset-2 hover:underline dark:text-gray-400"
						on:click={() => {
							showRaw = !showRaw;
						}}
					>
						{showRaw ? 'Hide raw payload' : 'Show raw payload'}
					</button>
				</div>

				{#if showRaw}
					<div
						class="mt-2 rounded-lg border border-gray-200/70 bg-gray-50/70 px-2.5 py-2 dark:border-gray-800/70 dark:bg-gray-900/35"
					>
						{#each entries as entry, idx (`raw-${entry.callId ?? idx}`)}
							<div class={idx === 0 ? '' : 'mt-3'}>
								<Markdown
									id={`${componentId}-sequential-thinking-raw-${idx}`}
									content={getRawPayloadMarkdown(entry, idx)}
									done={true}
									editCodeBlock={false}
								/>
							</div>
						{/each}
					</div>
				{/if}
			</div>
		{/if}
	</div>
</div>

<style>
	.sequential-thinking-markdown :global(p),
	.sequential-thinking-markdown :global(li) {
		line-height: 1.7;
	}
</style>

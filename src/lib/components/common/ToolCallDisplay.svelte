<script lang="ts">
	import { decode } from 'html-entities';
	import { v4 as uuidv4 } from 'uuid';

	import { getContext } from 'svelte';
	const i18n = getContext('i18n');

	import { slide } from 'svelte/transition';
	import { quintOut } from 'svelte/easing';

	import ChevronUp from '../icons/ChevronUp.svelte';
	import ChevronDown from '../icons/ChevronDown.svelte';
	import Spinner from './Spinner.svelte';
	import Markdown from '../chat/Messages/Markdown.svelte';
	import WrenchSolid from '../icons/WrenchSolid.svelte';
	import CheckCircle from '../icons/CheckCircle.svelte';
	import Image from './Image.svelte';
	import FullHeightIframe from './FullHeightIframe.svelte';
	import { settings } from '$lib/stores';

	export let id: string = '';
	export let attributes: {
		type?: string;
		id?: string;
		name?: string;
		arguments?: string;
		result?: string;
		files?: string;
		embeds?: string;
		done?: string;
	} = {};

	export let open = false;
	export let grouped = false;
	export let className = '';

	const RESULT_PREVIEW_LIMIT = 10000;
	let expandedResult = false;

	$: if (!open) expandedResult = false;
	export let buttonClassName = 'w-full transition';

	const componentId = id || uuidv4();

	function parseJSONString(str: string) {
		try {
			return parseJSONString(JSON.parse(str));
		} catch (e) {
			return str;
		}
	}

	function formatJSONString(str: string) {
		try {
			const parsed = parseJSONString(str);
			if (typeof parsed === 'object') {
				return JSON.stringify(parsed, null, 2);
			} else {
				return String(parsed);
			}
		} catch (e) {
			return str;
		}
	}

	function parseArguments(str: string): Record<string, unknown> | null {
		try {
			const parsed = parseJSONString(str);
			if (typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)) {
				return parsed as Record<string, unknown>;
			}
			return null;
		} catch {
			return null;
		}
	}

	function pickBestContent(...candidates: string[]) {
		return (
			candidates
				.map((candidate) => candidate ?? '')
				.filter((candidate) => candidate.trim().length > 0)
				.sort((left, right) => right.length - left.length)[0] ?? ''
		);
	}

	$: args = decode(attributes?.arguments ?? '');
	export let resultContent: string = '';

	$: result = pickBestContent(resultContent, decode(attributes?.result ?? ''));
	$: files = parseJSONString(decode(attributes?.files ?? ''));
	$: embeds = parseJSONString(decode(attributes?.embeds ?? ''));
	$: hasResult = result.trim().length > 0;
	$: hasFiles = Array.isArray(files) && files.length > 0;
	$: hasEmbeds = Array.isArray(embeds) && embeds.length > 0;
	$: isExecuting =
		Boolean(attributes?.done) &&
		attributes?.done !== 'true' &&
		!hasResult &&
		!hasFiles &&
		!hasEmbeds;
	$: isDone = attributes?.done === 'true' || (!isExecuting && (hasResult || hasFiles || hasEmbeds));

	$: parsedArgs = parseArguments(args);
	$: parsedResult = parseJSONString(result);
	$: toolName = attributes?.name ?? 'tool';
	$: containerToneClass = isExecuting
		? 'border-sky-300/70 dark:border-sky-700/60'
		: 'border-gray-200/70 dark:border-gray-800/70';
	$: headerToneClass = isExecuting
		? 'bg-sky-50/70 text-sky-900 dark:bg-sky-900/15 dark:text-sky-100'
		: 'bg-gray-50/70 text-gray-700 dark:bg-gray-900/30 dark:text-gray-200';
	$: dividerToneClass = isExecuting
		? 'border-sky-200/70 dark:border-sky-800/60'
		: 'border-gray-200/70 dark:border-gray-800/70';
	$: bodyToneClass = isExecuting
		? 'bg-sky-50/25 dark:bg-sky-900/5'
		: 'bg-gray-50/70 dark:bg-gray-900/30';
	$: iconToneClass = isExecuting
		? 'bg-sky-100/80 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300'
		: isDone
			? 'bg-emerald-100/80 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300'
			: 'bg-gray-100 text-gray-600 dark:bg-gray-850 dark:text-gray-300';
</script>

<div {id} class={className}>
	{#if !grouped && embeds && Array.isArray(embeds) && embeds.length > 0}
		<!-- Embed Mode: Show iframes without collapsible behavior -->
		<div class="py-1 w-full">
			<div
				class="tool-call-container w-full rounded-xl border border-gray-200/70 bg-gray-50/70 px-3 py-2 text-[13px] leading-5 text-gray-700 dark:border-gray-800/70 dark:bg-gray-900/30 dark:text-gray-200"
			>
				<div class="flex items-center gap-2">
					<div class="rounded-md bg-gray-100 p-1 text-gray-600 dark:bg-gray-850 dark:text-gray-300">
						<WrenchSolid className="size-3.5" />
					</div>
					<div class="line-clamp-1">
						{$i18n.t('Embedded Result from {{NAME}}', { NAME: toolName })}
					</div>
				</div>
			</div>
			{#each embeds as embed, idx}
				<div class="my-2" id={`${componentId}-tool-call-embed-${idx}`}>
					<FullHeightIframe
						src={embed}
						{args}
						allowScripts={true}
						allowForms={$settings?.iframeSandboxAllowForms ?? false}
						allowSameOrigin={$settings?.iframeSandboxAllowSameOrigin ?? false}
						allowPopups={true}
					/>
				</div>
			{/each}
		</div>
	{:else}
		<!-- Tool call display -->
		<div class={buttonClassName}>
			<div
				class="tool-call-container w-full overflow-hidden rounded-xl border {containerToneClass}"
			>
				<button
					type="button"
					class="tool-call-header w-full cursor-pointer px-3 py-2 text-left font-medium flex items-center justify-between gap-2 {headerToneClass}"
					on:click={() => {
						open = !open;
					}}
				>
					<div class="flex min-w-0 items-center gap-2">
						<div class="rounded-md p-1 {iconToneClass}">
							{#if isExecuting}
								<Spinner className="size-3.5" />
							{:else if isDone}
								<CheckCircle className="size-3.5" strokeWidth="2" />
							{:else}
								<WrenchSolid className="size-3.5" />
							{/if}
						</div>

						<div
							class="min-w-0 flex-1 line-clamp-1 text-[13px] leading-5 @md:text-sm {isExecuting
								? 'shimmer'
								: ''}"
						>
							<span class="@md:hidden">{toolName}</span>
							<span class="hidden @md:inline font-normal">
								{#if isDone}
									<Markdown
										id={`${componentId}-tool-call-title`}
										content={$i18n.t('View Result from **{{NAME}}**', {
											NAME: toolName
										})}
									/>
								{:else}
									<Markdown
										id={`${componentId}-tool-call-executing`}
										content={$i18n.t('Executing **{{NAME}}**...', {
											NAME: toolName
										})}
									/>
								{/if}
							</span>
						</div>
					</div>

					<div class="flex shrink-0 self-center translate-y-[1px]">
						{#if isDone}
							<ChevronDown
								strokeWidth="3.5"
								className="size-3.5 {open ? 'rotate-180' : ''} transition-transform"
							/>
						{:else if open}
							<ChevronUp strokeWidth="3.5" className="size-3.5" />
						{:else}
							<ChevronDown strokeWidth="3.5" className="size-3.5" />
						{/if}
					</div>
				</button>

				{#if open}
					<div
						class="tool-call-body border-t px-3 py-2 space-y-3 {dividerToneClass} {bodyToneClass}"
						transition:slide={{ duration: 300, easing: quintOut, axis: 'y' }}
					>
						<!-- Input -->
						{#if args}
							<div>
								<div
									class="text-[10px] uppercase tracking-wider font-medium text-gray-400 dark:text-gray-500 mb-1.5 px-1"
								>
									{$i18n.t('Input')}
								</div>

								{#if parsedArgs}
									<div class="px-1 space-y-0.5">
										{#each Object.entries(parsedArgs) as [key, value]}
											<div class="flex gap-2 text-xs py-0.5">
												<span class="font-medium text-gray-600 dark:text-gray-400 shrink-0"
													>{key}</span
												>
												<span class="text-gray-800 dark:text-gray-200 break-all"
													>{typeof value === 'object' ? JSON.stringify(value) : value}</span
												>
											</div>
										{/each}
									</div>
								{:else}
									<div class="w-full max-w-none!">
										<pre
											class="code-block-pre text-xs text-gray-600 dark:text-gray-300 whitespace-pre font-mono bg-gray-50 dark:bg-gray-900 rounded-lg p-2.5 overflow-x-auto">{formatJSONString(
												args
											)}</pre>
									</div>
								{/if}
							</div>
						{/if}

						<!-- Output -->
						{#if isDone && result}
							<div>
								<div
									class="text-[10px] uppercase tracking-wider font-medium text-gray-400 dark:text-gray-500 mb-1.5 px-1"
								>
									{$i18n.t('Output')}
								</div>
								<div class="w-full max-w-none!">
									{#if typeof parsedResult === 'object' && parsedResult !== null}
										<pre
											class="code-block-pre text-xs text-gray-600 dark:text-gray-300 whitespace-pre font-mono bg-gray-50 dark:bg-gray-900 rounded-lg p-2.5 overflow-x-auto">{JSON.stringify(
												parsedResult,
												null,
												2
											)}</pre>
									{:else}
										{@const resultStr = String(parsedResult)}
										{@const isTruncated =
											resultStr.length > RESULT_PREVIEW_LIMIT && !expandedResult}
										<pre
											class="code-block-pre text-xs text-gray-600 dark:text-gray-300 whitespace-pre-wrap break-words font-mono">{isTruncated
												? resultStr.slice(0, RESULT_PREVIEW_LIMIT)
												: resultStr}</pre>
										{#if isTruncated}
											<button
												class="mt-1 text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition"
												on:click|stopPropagation={() => {
													expandedResult = true;
												}}
											>
												{$i18n.t('Show all ({{COUNT}} characters)', {
													COUNT: resultStr.length.toLocaleString()
												})}
											</button>
										{/if}
									{/if}
								</div>
							</div>
						{/if}
					</div>
				{/if}
			</div>
		</div>
	{/if}

	<!-- Files display (images etc.) when done -->
	{#if isDone}
		{#if typeof files === 'object'}
			{#each files ?? [] as file, idx}
				{#if typeof file === 'string'}
					{#if file.startsWith('data:image/')}
						<Image id={`${componentId}-tool-call-result-${idx}`} src={file} alt="Image" />
					{/if}
				{:else if typeof file === 'object'}
					{#if (file.type === 'image' || (file?.content_type ?? '').startsWith('image/')) && file.url}
						<Image id={`${componentId}-tool-call-result-${idx}`} src={file.url} alt="Image" />
					{/if}
				{/if}
			{/each}
		{/if}
	{/if}
</div>

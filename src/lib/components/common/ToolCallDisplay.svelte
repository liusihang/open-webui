<script lang="ts">
	import { decode } from 'html-entities';
	import { v4 as uuidv4 } from 'uuid';

	import { getContext } from 'svelte';
	const i18n = getContext('i18n');

	import { slide } from 'svelte/transition';
	import { quintOut } from 'svelte/easing';

	import ChevronUp from '../icons/ChevronUp.svelte';
	import ChevronDown from '../icons/ChevronDown.svelte';
	import CheckCircle from '../icons/CheckCircle.svelte';
	import Wrench from '../icons/Wrench.svelte';
	import Spinner from './Spinner.svelte';
	import Markdown from '../chat/Messages/Markdown.svelte';
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
	export let className = '';
	export let buttonClassName = 'w-full transition';

	const componentId = id || uuidv4();

	function parseJSONString(str: string) {
		try {
			return parseJSONString(JSON.parse(str));
		} catch {
			return str;
		}
	}

	function formatJSONString(str: string) {
		try {
			const parsed = parseJSONString(str);
			// If parsed is an object/array, then it's valid JSON
			if (typeof parsed === 'object') {
				return JSON.stringify(parsed, null, 2);
			} else {
				return String(parsed);
			}
		} catch {
			// Not valid JSON, return as-is
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

	function formatArgumentValue(value: unknown) {
		if (typeof value === 'object' && value !== null) {
			return JSON.stringify(value);
		}
		return String(value);
	}

	// Decode and parse attributes
	$: args = decode(attributes?.arguments ?? '');
	$: result = decode(attributes?.result ?? '');
	$: files = parseJSONString(decode(attributes?.files ?? ''));
	$: embeds = parseJSONString(decode(attributes?.embeds ?? ''));
	$: isDone = attributes?.done === 'true';
	$: isExecuting = !!attributes?.done && attributes?.done !== 'true';
	$: toolName = attributes?.name ?? 'tool';
	$: formattedArgs = formatJSONString(args);
	$: formattedResult = formatJSONString(result);
	$: hasResult = String(result ?? '').trim() !== '';
	$: parsedArgs = parseArguments(args);
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
		: 'bg-emerald-100/80 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300';
</script>

<div {id} class={className}>
	{#if embeds && Array.isArray(embeds) && embeds.length > 0}
		<!-- Embed Mode: Show iframes without collapsible behavior -->
		<div class="py-1 w-full">
			<div
				class="tool-call-container w-full rounded-xl border border-gray-200/70 bg-gray-50/70 px-3 py-2 text-[13px] leading-5 text-gray-700 dark:border-gray-800/70 dark:bg-gray-900/30 dark:text-gray-200"
			>
				<div class="flex items-center gap-2">
					<div class="rounded-md bg-gray-100 p-1 text-gray-600 dark:bg-gray-850 dark:text-gray-300">
						<Wrench className="size-3.5" />
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
		<!-- Standard collapsible tool call display -->
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
							{:else}
								<CheckCircle className="size-3.5" />
							{/if}
						</div>

						<div class="min-w-0 text-base leading-6 line-clamp-1 {isExecuting ? 'shimmer' : ''}">
							{#if isDone}
								{$i18n.t('View Result from {{NAME}}', { NAME: toolName })}
							{:else}
								{$i18n.t('Executing {{NAME}}...', { NAME: toolName })}
							{/if}
						</div>
					</div>

					<div class="flex self-center translate-y-[1px]">
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
						class="tool-call-body border-t px-3 py-2 {dividerToneClass} {bodyToneClass}"
						transition:slide={{ duration: 300, easing: quintOut, axis: 'y' }}
					>
						{#if parsedArgs}
							<div class="space-y-2">
								<div class="text-xs font-semibold tracking-wide text-gray-500 dark:text-gray-400">
									{$i18n.t('Arguments')}
								</div>
								<div class="space-y-1">
									{#each Object.entries(parsedArgs) as [key, value]}
										<div class="flex gap-2 text-xs leading-5">
											<div
												class="font-medium text-gray-600 dark:text-gray-300 shrink-0 max-w-[45%] break-all"
											>
												{key}
											</div>
											<div class="text-gray-800 dark:text-gray-100 break-all">
												{formatArgumentValue(value)}
											</div>
										</div>
									{/each}
								</div>
							</div>
						{:else}
							<Markdown
								id={`${componentId}-tool-call-args`}
								content={`${$i18n.t('Arguments')}
\`\`\`json
${formattedArgs}
\`\`\``}
								editCodeBlock={false}
							/>
						{/if}

						{#if isDone && hasResult}
							<div class="mt-3">
								<Markdown
									id={`${componentId}-tool-call-result`}
									content={`${$i18n.t('Result')}
\`\`\`json
${formattedResult}
\`\`\``}
									editCodeBlock={false}
								/>
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

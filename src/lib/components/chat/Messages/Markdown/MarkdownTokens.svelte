<script lang="ts">
	import { decode } from 'html-entities';
	import { onMount, getContext } from 'svelte';
	const i18n = getContext('i18n');

	import fileSaver from 'file-saver';
	const { saveAs } = fileSaver;

	import { marked, type Token } from 'marked';
	import { copyToClipboard, unescapeHtml } from '$lib/utils';

	import { WEBUI_BASE_URL } from '$lib/constants';
	import { settings } from '$lib/stores';

	import CodeBlock from '$lib/components/chat/Messages/CodeBlock.svelte';
	import MarkdownInlineTokens from '$lib/components/chat/Messages/Markdown/MarkdownInlineTokens.svelte';
	import KatexRenderer from './KatexRenderer.svelte';
	import AlertRenderer, { alertComponent } from './AlertRenderer.svelte';
	import Collapsible from '$lib/components/common/Collapsible.svelte';
	import SequentialThinkingTimeline from '$lib/components/common/SequentialThinkingTimeline.svelte';
	import ToolCallDisplay from '$lib/components/common/ToolCallDisplay.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Download from '$lib/components/icons/Download.svelte';

	import HtmlToken from './HTMLToken.svelte';
	import Clipboard from '$lib/components/icons/Clipboard.svelte';

	export let id: string;
	export let tokens: Token[];
	export let top = true;
	export let attributes = {};
	export let sourceIds = [];

	export let done = true;

	export let save = false;
	export let preview = false;

	export let paragraphTag = 'p';

	export let editCodeBlock = true;
	export let topPadding = false;

	export let onSave: Function = () => {};
	export let onUpdate: Function = () => {};
	export let onPreview: Function = () => {};

	export let onTaskClick: Function = () => {};
	export let onSourceClick: Function = () => {};

	const headerComponent = (depth: number) => {
		return 'h' + depth;
	};

	type ToolCallTokenAttributes = {
		type?: string;
		id?: string;
		name?: string;
		arguments?: string;
		result?: string;
		done?: string;
	};

	type SequentialThinkingEntry = {
		callId?: string;
		toolName?: string;
		thoughtNumber?: number | null;
		totalThoughts?: number | null;
		thought?: string;
		nextThoughtNeeded?: boolean | null;
		isDone?: boolean | null;
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

	type RenderItem =
		| {
				type: 'token';
				token: Token;
		  }
		| {
				type: 'sequentialthinking_group';
				entries: SequentialThinkingEntry[];
				hasFollowingSequentialEntry: boolean;
		  };

	const SEQUENTIAL_THINKING_NAME_REGEX = /sequential[\s_-]*thinking|sequentialthinking/i;

	function parseNestedJson(value: unknown): unknown {
		let parsed = value;
		let iteration = 0;

		while (typeof parsed === 'string' && iteration < 6) {
			const trimmed = parsed.trim();
			if (!trimmed) {
				return '';
			}

			try {
				parsed = JSON.parse(trimmed);
				iteration += 1;
			} catch {
				break;
			}
		}

		return parsed;
	}

	function toObject(value: unknown): Record<string, unknown> | null {
		if (value && typeof value === 'object' && !Array.isArray(value)) {
			return value as Record<string, unknown>;
		}
		return null;
	}

	function toNumber(value: unknown): number | null {
		if (typeof value === 'number' && Number.isFinite(value)) {
			return value;
		}
		if (typeof value === 'string' && value.trim() !== '') {
			const parsed = Number(value);
			return Number.isFinite(parsed) ? parsed : null;
		}
		return null;
	}

	function toBoolean(value: unknown): boolean | null {
		if (typeof value === 'boolean') {
			return value;
		}
		if (typeof value === 'string') {
			const normalized = value.trim().toLowerCase();
			if (normalized === 'true') {
				return true;
			}
			if (normalized === 'false') {
				return false;
			}
		}
		return null;
	}

	function getSequentialThinkingEntry(token: Token): SequentialThinkingEntry | null {
		const detailsToken = token as Token & { attributes?: ToolCallTokenAttributes };
		const tokenAttributes = detailsToken?.attributes;

		if (detailsToken?.type !== 'details' || tokenAttributes?.type !== 'tool_calls') {
			return null;
		}

		const toolName = tokenAttributes?.name ?? '';
		if (!SEQUENTIAL_THINKING_NAME_REGEX.test(toolName)) {
			return null;
		}

		const rawArguments = decode(tokenAttributes?.arguments ?? '');
		const rawResult = decode(tokenAttributes?.result ?? '');
		const parsedArguments = parseNestedJson(rawArguments);
		const parsedResult = parseNestedJson(rawResult);

		const argumentObject = toObject(parsedArguments);
		const resultObject = toObject(parsedResult);

		const thoughtNumber = toNumber(argumentObject?.thoughtNumber ?? resultObject?.thoughtNumber);
		const totalThoughts = toNumber(argumentObject?.totalThoughts ?? resultObject?.totalThoughts);
		const nextThoughtNeeded = toBoolean(
			argumentObject?.nextThoughtNeeded ?? resultObject?.nextThoughtNeeded
		);
		const isDone = toBoolean(tokenAttributes?.done);
		const branchFromThought = toNumber(argumentObject?.branchFromThought);
		const revisesThought = toNumber(argumentObject?.revisesThought);
		const thoughtHistoryLength = toNumber(resultObject?.thoughtHistoryLength);
		const thought = typeof argumentObject?.thought === 'string' ? argumentObject.thought : '';
		const branchId =
			typeof argumentObject?.branchId === 'string' && argumentObject.branchId.trim()
				? argumentObject.branchId
				: null;

		return {
			callId: tokenAttributes?.id,
			toolName,
			thoughtNumber,
			totalThoughts,
			thought,
			nextThoughtNeeded,
			isDone,
			branchId,
			branchFromThought,
			isRevision: toBoolean(argumentObject?.isRevision) === true,
			revisesThought,
			thoughtHistoryLength,
			rawArguments,
			rawResult,
			parsedArguments,
			parsedResult
		};
	}

	function getRenderItems(tokenList: Token[] = []): RenderItem[] {
		const items: RenderItem[] = [];
		let idx = 0;

		while (idx < tokenList.length) {
			const token = tokenList[idx];
			const firstEntry = getSequentialThinkingEntry(token);

			if (!firstEntry) {
				items.push({ type: 'token', token });
				idx += 1;
				continue;
			}

			const entries: SequentialThinkingEntry[] = [firstEntry];
			const groupBranchId = firstEntry.branchId;
			idx += 1;

			while (idx < tokenList.length) {
				const nextEntry = getSequentialThinkingEntry(tokenList[idx]);
				if (!nextEntry) {
					break;
				}

				// Keep branch-specific traces grouped separately to avoid mixing parallel branches.
				if (groupBranchId && nextEntry.branchId && groupBranchId !== nextEntry.branchId) {
					break;
				}

				entries.push(nextEntry);
				idx += 1;
			}

			const hasFollowingSequentialEntry = tokenList
				.slice(idx)
				.some((candidate) => Boolean(getSequentialThinkingEntry(candidate)));

			items.push({ type: 'sequentialthinking_group', entries, hasFollowingSequentialEntry });
		}

		return items;
	}

	$: renderItems = getRenderItems(tokens ?? []);

	const exportTableToCSVHandler = (token, tokenIdx = 0) => {
		console.log('Exporting table to CSV');

		// Extract header row text, decode HTML entities, and escape for CSV.
		const header = token.header.map(
			(headerCell) => `"${decode(headerCell.text).replace(/"/g, '""')}"`
		);

		// Create an array for rows that will hold the mapped cell text.
		const rows = token.rows.map((row) =>
			row.map((cell) => {
				// Map tokens into a single text
				const cellContent = cell.tokens.map((token) => token.text).join('');
				// Decode HTML entities and escape double quotes, wrap in double quotes
				return `"${decode(cellContent).replace(/"/g, '""')}"`;
			})
		);

		// Combine header and rows
		const csvData = [header, ...rows];

		// Join the rows using commas (,) as the separator and rows using newline (\n).
		const csvContent = csvData.map((row) => row.join(',')).join('\n');

		// Log rows and CSV content to ensure everything is correct.
		console.log(csvData);
		console.log(csvContent);

		// To handle Unicode characters, you need to prefix the data with a BOM:
		const bom = '\uFEFF'; // BOM for UTF-8

		// Create a new Blob prefixed with the BOM to ensure proper Unicode encoding.
		const blob = new Blob([bom + csvContent], { type: 'text/csv;charset=UTF-8' });

		// Use FileSaver.js's saveAs function to save the generated CSV file.
		saveAs(blob, `table-${id}-${tokenIdx}.csv`);
	};
</script>

<!-- {JSON.stringify(tokens)} -->
{#each renderItems as item, tokenIdx (tokenIdx)}
	{#if item.type === 'sequentialthinking_group'}
		<SequentialThinkingTimeline
			id={`${id}-${tokenIdx}-st`}
			entries={item.entries}
			hasFollowingSequentialEntry={item.hasFollowingSequentialEntry}
			className="w-full my-1"
		/>
	{:else}
		{@const token = item.token}
		{#if token.type === 'hr'}
		<hr class=" border-gray-100/30 dark:border-gray-850/30" />
		{:else if token.type === 'heading'}
		<svelte:element this={headerComponent(token.depth)} dir="auto">
			<MarkdownInlineTokens
				id={`${id}-${tokenIdx}-h`}
				tokens={token.tokens}
				{done}
				{sourceIds}
				{onSourceClick}
			/>
		</svelte:element>
		{:else if token.type === 'code'}
		{#if token.raw.includes('```')}
			<CodeBlock
				id={`${id}-${tokenIdx}`}
				collapsed={$settings?.collapseCodeBlocks ?? false}
				{token}
				lang={token?.lang ?? ''}
				code={token?.text ?? ''}
				{attributes}
				{save}
				{preview}
				edit={editCodeBlock}
				stickyButtonsClassName={topPadding ? 'top-10' : 'top-0'}
				onSave={(value) => {
					onSave({
						raw: token.raw,
						oldContent: token.text,
						newContent: value
					});
				}}
				{onUpdate}
				{onPreview}
			/>
		{:else}
			{token.text}
		{/if}
		{:else if token.type === 'table'}
		<div class="relative w-full group mb-2">
			<div class="scrollbar-hidden relative overflow-x-auto max-w-full">
				<table
					class=" w-full text-sm text-start text-gray-500 dark:text-gray-400 max-w-full rounded-xl"
					dir="auto"
				>
					<thead
						class="text-xs text-gray-700 uppercase bg-white dark:bg-gray-900 dark:text-gray-400 border-none"
					>
						<tr class="">
							{#each token.header as header, headerIdx}
								<th
									scope="col"
									class="px-2.5! py-2! cursor-pointer border-b border-gray-100! dark:border-gray-800!"
									style={token.align[headerIdx] ? `text-align: ${token.align[headerIdx]}` : ''}
								>
									<div class="gap-1.5 text-start">
										<div class="shrink-0 break-normal">
											<MarkdownInlineTokens
												id={`${id}-${tokenIdx}-header-${headerIdx}`}
												tokens={header.tokens}
												{done}
												{sourceIds}
												{onSourceClick}
											/>
										</div>
									</div>
								</th>
							{/each}
						</tr>
					</thead>
					<tbody>
						{#each token.rows as row, rowIdx}
							<tr class="bg-white dark:bg-gray-900 text-xs">
								{#each row ?? [] as cell, cellIdx}
									<td
										class="px-3! py-2! text-gray-900 dark:text-white w-max {token.rows.length -
											1 ===
										rowIdx
											? ''
											: 'border-b border-gray-50! dark:border-gray-850!'}"
										style={token.align[cellIdx] ? `text-align: ${token.align[cellIdx]}` : ''}
									>
										<div class="break-normal">
											<MarkdownInlineTokens
												id={`${id}-${tokenIdx}-row-${rowIdx}-${cellIdx}`}
												tokens={cell.tokens}
												{done}
												{sourceIds}
												{onSourceClick}
											/>
										</div>
									</td>
								{/each}
							</tr>
						{/each}
					</tbody>
				</table>
			</div>

			<div class=" absolute top-1 right-1.5 z-20 invisible group-hover:visible flex gap-0.5">
				<Tooltip content={$i18n.t('Copy')}>
					<button
						class="p-1 rounded-lg bg-transparent transition"
						on:click={(e) => {
							e.stopPropagation();
							copyToClipboard(token.raw.trim(), null, $settings?.copyFormatted ?? false);
						}}
					>
						<Clipboard className=" size-3.5" strokeWidth="1.5" />
					</button>
				</Tooltip>

				<Tooltip content={$i18n.t('Export to CSV')}>
					<button
						class="p-1 rounded-lg bg-transparent transition"
						on:click={(e) => {
							e.stopPropagation();
							exportTableToCSVHandler(token, tokenIdx);
						}}
					>
						<Download className=" size-3.5" strokeWidth="1.5" />
					</button>
				</Tooltip>
			</div>
		</div>
		{:else if token.type === 'blockquote'}
		{@const alert = alertComponent(token)}
		{#if alert}
			<AlertRenderer {token} {alert} />
		{:else}
			<blockquote dir="auto">
				<svelte:self
					id={`${id}-${tokenIdx}`}
					tokens={token.tokens}
					{done}
					{editCodeBlock}
					{onTaskClick}
					{sourceIds}
					{onSourceClick}
				/>
			</blockquote>
		{/if}
		{:else if token.type === 'list'}
		{#if token.ordered}
			<ol start={token.start || 1} dir="auto">
				{#each token.items as item, itemIdx}
					<li class="text-start">
						{#if item?.task}
							<input
								class=" translate-y-[1px] -translate-x-1"
								type="checkbox"
								checked={item.checked}
								on:change={(e) => {
									onTaskClick({
										id: id,
										token: token,
										tokenIdx: tokenIdx,
										item: item,
										itemIdx: itemIdx,
										checked: e.target.checked
									});
								}}
							/>
						{/if}

						<svelte:self
							id={`${id}-${tokenIdx}-${itemIdx}`}
							tokens={item.tokens}
							top={token.loose}
							{done}
							{editCodeBlock}
							{onTaskClick}
							{sourceIds}
							{onSourceClick}
						/>
					</li>
				{/each}
			</ol>
		{:else}
			<ul dir="auto" class="">
				{#each token.items as item, itemIdx}
					<li class="text-start {item?.task ? 'flex -translate-x-6.5 gap-3 ' : ''}">
						{#if item?.task}
							<input
								class=""
								type="checkbox"
								checked={item.checked}
								on:change={(e) => {
									onTaskClick({
										id: id,
										token: token,
										tokenIdx: tokenIdx,
										item: item,
										itemIdx: itemIdx,
										checked: e.target.checked
									});
								}}
							/>

							<div>
								<svelte:self
									id={`${id}-${tokenIdx}-${itemIdx}`}
									tokens={item.tokens}
									top={token.loose}
									{done}
									{editCodeBlock}
									{onTaskClick}
									{sourceIds}
									{onSourceClick}
								/>
							</div>
						{:else}
							<svelte:self
								id={`${id}-${tokenIdx}-${itemIdx}`}
								tokens={item.tokens}
								top={token.loose}
								{done}
								{editCodeBlock}
								{onTaskClick}
								{sourceIds}
								{onSourceClick}
							/>
						{/if}
					</li>
				{/each}
			</ul>
		{/if}
		{:else if token.type === 'details'}
		{@const textContent = decode(token.text || '')
			.replace(/<summary>.*?<\/summary>/gi, '')
			.trim()}
		{@const isReasoningDetails = token?.attributes?.type === 'reasoning'}
		{@const reasoningDone = String(token?.attributes?.done ?? 'false') === 'true'}

		{#if token?.attributes?.type === 'tool_calls'}
			<!-- Tool calls have dedicated handling with ToolCallDisplay component -->
			<ToolCallDisplay
				id={`${id}-${tokenIdx}-tc`}
				attributes={token.attributes}
				open={false}
				className="w-full my-1"
			/>
		{:else if textContent.length > 0}
			<Collapsible
				title={token.summary}
				open={isReasoningDetails
					? reasoningDone
						? ($settings?.expandDetails ?? false)
						: true
					: ($settings?.expandDetails ?? false)}
				attributes={token?.attributes}
				className="w-full my-1"
				buttonClassName={isReasoningDetails
					? 'w-full transition'
					: 'w-fit text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition'}
				dir="auto"
			>
				<div
					class="mb-1.5 {isReasoningDetails ? 'reasoning-content text-base' : ''}"
					slot="content"
				>
					<svelte:self
						id={`${id}-${tokenIdx}-d`}
						tokens={marked.lexer(decode(token.text))}
						attributes={token?.attributes}
						{done}
						{editCodeBlock}
						{onTaskClick}
						{sourceIds}
						{onSourceClick}
					/>
				</div>
			</Collapsible>
		{:else}
			<Collapsible
				title={token.summary}
				open={false}
				disabled={true}
				attributes={token?.attributes}
				className="w-full my-1"
				dir="auto"
			/>
		{/if}
		{:else if token.type === 'html'}
		<HtmlToken {id} {token} {onSourceClick} />
		{:else if token.type === 'iframe'}
		<iframe
			src="{WEBUI_BASE_URL}/api/v1/files/{token.fileId}/content"
			title={token.fileId}
			width="100%"
			frameborder="0"
			on:load={(e) => {
				try {
					e.currentTarget.style.height =
						e.currentTarget.contentWindow.document.body.scrollHeight + 20 + 'px';
				} catch {}
			}}
		></iframe>
		{:else if token.type === 'paragraph'}
		{#if paragraphTag == 'span'}
			<span dir="auto">
				<MarkdownInlineTokens
					id={`${id}-${tokenIdx}-p`}
					tokens={token.tokens ?? []}
					{done}
					{sourceIds}
					{onSourceClick}
				/>
			</span>
		{:else}
			<p dir="auto">
				<MarkdownInlineTokens
					id={`${id}-${tokenIdx}-p`}
					tokens={token.tokens ?? []}
					{done}
					{sourceIds}
					{onSourceClick}
				/>
			</p>
		{/if}
		{:else if token.type === 'text'}
		{#if top}
			<p>
				{#if token.tokens}
					<MarkdownInlineTokens
						id={`${id}-${tokenIdx}-t`}
						tokens={token.tokens}
						{done}
						{sourceIds}
						{onSourceClick}
					/>
				{:else}
					{unescapeHtml(token.text)}
				{/if}
			</p>
		{:else if token.tokens}
			<MarkdownInlineTokens
				id={`${id}-${tokenIdx}-p`}
				tokens={token.tokens ?? []}
				{done}
				{sourceIds}
				{onSourceClick}
			/>
		{:else}
			{unescapeHtml(token.text)}
		{/if}
		{:else if token.type === 'inlineKatex'}
		{#if token.text}
			<KatexRenderer content={token.text} displayMode={token?.displayMode ?? false} />
		{/if}
		{:else if token.type === 'blockKatex'}
		{#if token.text}
			<KatexRenderer content={token.text} displayMode={token?.displayMode ?? false} />
		{/if}
			{:else if token.type === 'space'}
			<div class="my-2"></div>
		{:else}
		{console.log('Unknown token', token)}
		{/if}
	{/if}
{/each}

<style>
	.reasoning-content {
		padding-top: 0.5rem;
	}

	.reasoning-content :global(p),
	.reasoning-content :global(li) {
		line-height: 1.75;
	}
</style>

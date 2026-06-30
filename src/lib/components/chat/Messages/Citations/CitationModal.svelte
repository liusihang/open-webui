<script lang="ts">
	import { getContext } from 'svelte';
	import Modal from '$lib/components/common/Modal.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Markdown from '$lib/components/chat/Messages/Markdown.svelte';
	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import { settings, config } from '$lib/stores';
	import { injectCsp } from '$lib/utils/csp';

	import XMark from '$lib/components/icons/XMark.svelte';
	import Textarea from '$lib/components/common/Textarea.svelte';

	const i18n = getContext('i18n');

	const CONTENT_PREVIEW_LIMIT = 10000;
	const PRIMARY_PREVIEW_LIMIT = 1200;
	let expandedDocs: Set<number> = new Set();

	export let show = false;
	export let citation;
	export let showPercentage = false;
	export let showRelevance = false;

	let mergedDocuments = [];
	let primaryDocument: any = null;
	let primaryPreviewText = '';

	function calculatePercentage(distance: number) {
		if (typeof distance !== 'number') return null;
		if (distance < 0) return 0;
		if (distance > 1) return 100;
		return Math.round(distance * 10000) / 100;
	}

	function getRelevanceColor(percentage: number) {
		if (percentage >= 80)
			return 'bg-green-200 dark:bg-green-800 text-green-800 dark:text-green-200';
		if (percentage >= 60)
			return 'bg-yellow-200 dark:bg-yellow-800 text-yellow-800 dark:text-yellow-200';
		if (percentage >= 40)
			return 'bg-orange-200 dark:bg-orange-800 text-orange-800 dark:text-orange-200';
		return 'bg-red-200 dark:bg-red-800 text-red-800 dark:text-red-200';
	}

	$: if (citation) {
		expandedDocs = new Set();
		mergedDocuments = citation.document?.map((c, i) => {
			return {
				source: citation.source,
				document: c,
				metadata: citation.metadata?.[i],
				distance: citation.distances?.[i]
			};
		});
		if (mergedDocuments.every((doc) => doc.distance !== undefined)) {
			mergedDocuments = mergedDocuments.sort(
				(a, b) => (b.distance ?? Infinity) - (a.distance ?? Infinity)
			);
		}
	}

	$: primaryDocument = mergedDocuments?.[0] ?? null;
	$: primaryPreviewText = trimPreviewText(getPrimaryPreviewText());

	const decodeString = (str: string) => {
		try {
			return decodeURIComponent(str);
		} catch {
			return str;
		}
	};

	const getTextFragmentUrl = (doc: any): string | null => {
		const { metadata, source, document: content } = doc ?? {};
		const { file_id, page } = metadata ?? {};
		const sourceUrl = source?.url;

		const baseUrl = file_id
			? `${WEBUI_API_BASE_URL}/files/${file_id}/content${page !== undefined ? `#page=${page + 1}` : ''}`
			: sourceUrl?.includes('http')
				? sourceUrl
				: null;

		if (!baseUrl || !content) return baseUrl;

		// Extract first and last words for text fragment, filtering out URLs and emojis
		const words = content
			.trim()
			.replace(/\s+/g, ' ')
			.split(' ')
			.filter((w: string) => w.length > 0 && !/https?:\/\/|[\u{1F300}-\u{1F9FF}]/u.test(w));

		if (words.length === 0) return baseUrl;

		const clean = (w: string) => w.replace(/[^\w]/g, '');
		const first = clean(words[0]);
		const last = clean(words.at(-1));
		const fragment = words.length === 1 ? first : `${first},${last}`;

		return fragment ? `${baseUrl}#:~:text=${fragment}` : baseUrl;
	};

	const getDocumentText = (content: any): string => {
		if (typeof content === 'string') {
			return content.trim().replace(/\n\n+/g, '\n\n');
		}
		if (content === undefined || content === null) {
			return '';
		}
		try {
			return JSON.stringify(content, null, 2);
		} catch {
			return String(content);
		}
	};

	const trimPreviewText = (content: string, limit = PRIMARY_PREVIEW_LIMIT) => {
		const normalized = content.trim().replace(/\n\n+/g, '\n\n');
		if (normalized.length <= limit) {
			return normalized;
		}
		return `${normalized.slice(0, limit).trimEnd()}…`;
	};

	const getPrimaryPreviewText = () => {
		const previewText =
			typeof citation?.preview?.text === 'string' && citation.preview.text.trim().length > 0
				? citation.preview.text
				: typeof citation?.preview?.caption === 'string' &&
					  citation.preview.caption.trim().length > 0
					? citation.preview.caption
					: '';
		if (previewText.length > 0) {
			return previewText;
		}
		const primaryDocument = mergedDocuments?.[0];
		return getDocumentText(primaryDocument?.document);
	};
</script>

<Modal size="lg" bind:show>
	<div class="overflow-hidden rounded-lg">
		<div
			class="flex justify-between gap-3 border-b border-gray-100 px-4.5 py-3 dark:border-gray-800 dark:text-gray-300"
		>
			<div class="min-w-0 self-center flex items-center gap-2 text-base font-semibold">
				<span
					class="inline-flex h-6 shrink-0 items-center rounded-md border border-blue-200 bg-blue-50 px-2 text-xs font-semibold text-blue-700 dark:border-blue-500/30 dark:bg-blue-500/10 dark:text-blue-200"
				>
					{citation?.preview?.type === 'image' ? $i18n.t('Image') : $i18n.t('Source')}
				</span>
				{#if citation?.source?.name}
					{#if primaryDocument?.metadata?.file_id || primaryDocument?.source?.url?.includes('http')}
						<Tooltip
							className="w-fit"
							content={primaryDocument?.source?.url?.includes('http')
								? $i18n.t('Open link')
								: $i18n.t('Open file')}
							placement="top-start"
							tippyOptions={{ duration: [500, 0] }}
						>
							<a
								class="grow line-clamp-1 underline hover:text-gray-500 dark:hover:text-gray-100"
								href={primaryDocument?.metadata?.file_id
									? `${WEBUI_API_BASE_URL}/files/${primaryDocument?.metadata?.file_id}/content${primaryDocument?.metadata?.page !== undefined ? `#page=${primaryDocument.metadata.page + 1}` : ''}`
									: primaryDocument?.source?.url?.includes('http')
										? (primaryDocument?.source?.url ?? '#')
										: `#`}
								target="_blank"
							>
								{decodeString(citation?.source?.name)}
							</a>
						</Tooltip>
					{:else}
						{decodeString(citation?.source?.name)}
					{/if}
				{:else}
					{$i18n.t('Citation')}
				{/if}
			</div>
			<button
				class="self-center rounded-md p-1 text-gray-500 transition hover:bg-gray-100 hover:text-gray-900 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-100"
				aria-label={$i18n.t('Close citation modal')}
				on:click={() => {
					show = false;
				}}
			>
				<XMark className={'size-5'} />
			</button>
		</div>

		<div class="flex flex-col md:flex-row w-full px-5 py-5 md:space-x-4">
			<div
				class="flex max-h-[28rem] w-full flex-col gap-3 overflow-y-scroll scrollbar-thin dark:text-gray-200"
			>
				{#if citation?.preview?.type === 'image' && citation.preview.content_url}
					<div class="space-y-2">
						<img
							src={citation.preview.content_url}
							alt={citation.preview.caption ||
								citation.preview.source_name ||
								citation?.source?.name ||
								''}
							class="max-h-[18rem] w-full rounded-md object-contain bg-gray-50 dark:bg-gray-850"
						/>
						<div class="space-y-1 text-sm text-gray-600 dark:text-gray-300">
							{#if citation.preview.caption}
								<div class="whitespace-pre-wrap">{citation.preview.caption}</div>
							{/if}
							<div class="flex flex-wrap gap-x-2 gap-y-1 text-xs text-gray-500 dark:text-gray-400">
								{#if citation.preview.source_name}
									<span>{citation.preview.source_name}</span>
								{/if}
								<span>{citation.preview.type === 'image' ? $i18n.t('Image') : $i18n.t('Text')}</span
								>
								{#if Number.isInteger(citation.preview.page_index)}
									<span>{$i18n.t('page')} {citation.preview.page_index + 1}</span>
								{/if}
							</div>
						</div>
					</div>
				{:else if primaryPreviewText}
					<div
						class="rounded-lg border border-gray-200 bg-white p-3 shadow-sm dark:border-gray-800 dark:bg-gray-900"
					>
						{#if $settings?.renderMarkdownInPreviews ?? true}
							<div class="text-sm prose dark:prose-invert markdown-prose-sm min-w-full max-w-full">
								<Markdown
									content={primaryPreviewText}
									id="citation-preview-{citation?.source?.name || 'citation'}"
								/>
							</div>
						{:else}
							<pre class="text-sm whitespace-pre-wrap break-words font-sans dark:text-gray-300">
								{primaryPreviewText}
							</pre>
						{/if}
					</div>
				{/if}

				<details
					class="rounded-lg border border-gray-200 bg-gray-50/70 p-3 dark:border-gray-800 dark:bg-gray-900/70"
				>
					<summary
						class="flex cursor-pointer select-none list-none items-center justify-between gap-2 text-sm font-semibold text-gray-700 dark:text-gray-200"
					>
						<span>{$i18n.t('Details')}</span>
					</summary>
					<div class="mt-3 space-y-3">
						{#if citation?.preview?.ocr_text}
							<div class="space-y-1">
								<div class="text-sm font-medium dark:text-gray-300">
									{$i18n.t('OCR')}
								</div>
								<pre
									class="text-sm whitespace-pre-wrap break-words font-sans text-gray-500 dark:text-gray-400">{citation
										.preview.ocr_text}</pre>
							</div>
						{/if}

						{#each mergedDocuments as document, documentIdx}
							<div class="flex flex-col w-full gap-2">
								{#if document.metadata?.parameters}
									<div>
										<div class="text-sm font-medium dark:text-gray-300 mb-1">
											{$i18n.t('Parameters')}
										</div>

										<Textarea readonly value={JSON.stringify(document.metadata.parameters, null, 2)}
										></Textarea>
									</div>
								{/if}

								<div>
									<div
										class=" text-sm font-medium dark:text-gray-300 flex items-center gap-2 w-fit mb-1"
									>
										{#if document.source?.url?.includes('http')}
											{@const snippetUrl = getTextFragmentUrl(document)}
											{#if snippetUrl}
												<a
													href={snippetUrl}
													target="_blank"
													class="underline hover:text-gray-500 dark:hover:text-gray-100"
													>{$i18n.t('Content')}</a
												>
											{:else}
												{$i18n.t('Content')}
											{/if}
										{:else}
											{$i18n.t('Content')}
										{/if}

										{#if showRelevance && document.distance !== undefined}
											<Tooltip
												className="w-fit"
												content={$i18n.t('Relevance')}
												placement="top-start"
												tippyOptions={{ duration: [500, 0] }}
											>
												<div class="text-sm my-1 dark:text-gray-400 flex items-center gap-2 w-fit">
													{#if showPercentage}
														{@const percentage = calculatePercentage(document.distance)}

														{#if typeof percentage === 'number'}
															<span
																class={`px-1 rounded-sm font-medium ${getRelevanceColor(percentage)}`}
															>
																{percentage.toFixed(2)}%
															</span>
														{/if}
													{:else if typeof document?.distance === 'number'}
														<span class="text-gray-500 dark:text-gray-500">
															({(document?.distance ?? 0).toFixed(4)})
														</span>
													{/if}
												</div>
											</Tooltip>
										{/if}

										{#if Number.isInteger(document?.metadata?.page)}
											<span class="text-sm text-gray-500 dark:text-gray-400">
												({$i18n.t('page')}
												{document.metadata.page + 1})
											</span>
										{/if}
									</div>

									{#if document.metadata?.html}
										<iframe
											class="w-full border-0 h-auto rounded-none"
											sandbox="allow-scripts allow-forms{($settings?.iframeSandboxAllowSameOrigin ??
											false)
												? ' allow-same-origin'
												: ''}"
											srcdoc={injectCsp(document.document, $config?.ui?.iframe_csp ?? '')}
											title={$i18n.t('Content')}
										></iframe>
									{:else}
										{@const rawContent = getDocumentText(document.document)}
										{@const isTruncated =
											($settings?.renderMarkdownInPreviews ?? true) &&
											rawContent.length > CONTENT_PREVIEW_LIMIT &&
											!expandedDocs.has(documentIdx)}
										{#if document.metadata?.evidence_ref || citation?.preview}
											<pre
												class="text-sm dark:text-gray-400 whitespace-pre-wrap break-words font-sans">{isTruncated
													? rawContent.slice(0, CONTENT_PREVIEW_LIMIT)
													: rawContent}</pre>
											{#if isTruncated}
												<button
													class="mt-2 rounded-md border border-gray-200 bg-white px-2.5 py-1 text-xs font-medium text-gray-600 transition hover:border-gray-300 hover:text-gray-900 dark:border-gray-700 dark:bg-gray-850 dark:text-gray-300 dark:hover:text-gray-100"
													on:click={() => {
														expandedDocs.add(documentIdx);
														expandedDocs = expandedDocs;
													}}
												>
													{$i18n.t('Show all ({{COUNT}} characters)', {
														COUNT: rawContent.length.toLocaleString()
													})}
												</button>
											{/if}
										{:else if $settings?.renderMarkdownInPreviews ?? true}
											<div
												class="text-sm prose dark:prose-invert markdown-prose-sm min-w-full max-w-full"
											>
												<Markdown
													content={isTruncated
														? rawContent.slice(0, CONTENT_PREVIEW_LIMIT)
														: rawContent}
													id="citation-{documentIdx}"
												/>
											</div>
											{#if isTruncated}
												<button
													class="mt-2 rounded-md border border-gray-200 bg-white px-2.5 py-1 text-xs font-medium text-gray-600 transition hover:border-gray-300 hover:text-gray-900 dark:border-gray-700 dark:bg-gray-850 dark:text-gray-300 dark:hover:text-gray-100"
													on:click={() => {
														expandedDocs.add(documentIdx);
														expandedDocs = expandedDocs;
													}}
												>
													{$i18n.t('Show all ({{COUNT}} characters)', {
														COUNT: rawContent.length.toLocaleString()
													})}
												</button>
											{/if}
										{:else}
											<pre class="text-sm dark:text-gray-400 whitespace-pre-line">{rawContent}</pre>
										{/if}
									{/if}
								</div>
							</div>
						{/each}
					</div>
				</details>
			</div>
		</div>
	</div>
</Modal>

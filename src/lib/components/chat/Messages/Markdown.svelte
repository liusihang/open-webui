<script>
	import { onDestroy } from 'svelte';
	import { marked } from 'marked';
	import { replaceTokens, processResponseContent } from '$lib/utils';
	import { user } from '$lib/stores';

	import markedExtension from '$lib/utils/marked/extension';
	import markedKatexExtension from '$lib/utils/marked/katex-extension';
	import { disableSingleTilde } from '$lib/utils/marked/strikethrough-extension';
	import { mentionExtension } from '$lib/utils/marked/mention-extension';

	import MarkdownTokens from './Markdown/MarkdownTokens.svelte';
	import footnoteExtension from '$lib/utils/marked/footnote-extension';
	import citationExtension from '$lib/utils/marked/citation-extension';
	import {
		getLargeMarkdownPreview,
		shouldDeferMarkdownParsing
	} from './markdownPerformance';

	export let id = '';
	export let content;
	export let done = true;
	export let model = null;
	export let save = false;
	export let preview = false;

	export let paragraphTag = 'p';
	export let editCodeBlock = true;
	export let topPadding = false;

	export let sourceIds = [];

	export let onSave = () => {};
	export let onUpdate = () => {};

	export let onPreview = () => {};

	export let onSourceClick = () => {};
	export let onTaskClick = () => {};

	let tokens = [];
	let pendingUpdate = null;
	let pendingTimeout = null;
	let parsingDeferred = false;
	let previewContent = '';
	let lastContent = '';
	let lastParsedContent = '';

	const options = {
		throwOnError: false,
		breaks: true
	};

	marked.use(markedKatexExtension(options));
	marked.use(markedExtension(options));
	marked.use(citationExtension(options));
	marked.use(footnoteExtension(options));
	marked.use(disableSingleTilde);
	marked.use({
		extensions: [
			mentionExtension({ triggerChar: '@' }),
			mentionExtension({ triggerChar: '#' }),
			mentionExtension({ triggerChar: '$' })
		]
	});

	const cancelPendingWork = () => {
		cancelAnimationFrame(pendingUpdate);
		pendingUpdate = null;
		clearTimeout(pendingTimeout);
		pendingTimeout = null;
	};

	const parseTokens = () => {
		if (content === lastContent) return;
		lastContent = content;

		const processed = replaceTokens(processResponseContent(content), model?.name, $user?.name);
		if (processed === lastParsedContent) return;
		lastParsedContent = processed;

		tokens = marked.lexer(processed);
		parsingDeferred = false;
		previewContent = '';
	};

	const updateHandler = (content) => {
		if (!content) {
			cancelPendingWork();
			tokens = [];
			parsingDeferred = false;
			previewContent = '';
			lastContent = '';
			lastParsedContent = '';
			return;
		}

		cancelPendingWork();

		if (shouldDeferMarkdownParsing(content, done, preview)) {
			parsingDeferred = true;
			previewContent = getLargeMarkdownPreview(content);
			pendingTimeout = setTimeout(() => {
				pendingTimeout = null;
				parseTokens();
			}, 0);
			return;
		}

		if (done) {
			parseTokens();
		} else if (!pendingUpdate) {
			pendingUpdate = requestAnimationFrame(() => {
				pendingUpdate = null;
				parseTokens();
			});
		}
	};

	$: updateHandler(content);

	// Throttle parsing to once per animation frame while streaming
	$: onDestroy(() => {
		cancelPendingWork();
	});
</script>

{#if parsingDeferred && tokens.length === 0}
	<div class="space-y-2">
		<div class="text-xs text-gray-500 dark:text-gray-400">Formatting long response...</div>
		<pre class="text-sm whitespace-pre-wrap break-words text-gray-700 dark:text-gray-300">{previewContent}</pre>
	</div>
{:else}
	{#key id}
		<MarkdownTokens
			{tokens}
			{id}
			{done}
			{save}
			{preview}
			{paragraphTag}
			{editCodeBlock}
			{sourceIds}
			{topPadding}
			{onTaskClick}
			{onSourceClick}
			{onSave}
			{onUpdate}
			{onPreview}
		/>
	{/key}
{/if}

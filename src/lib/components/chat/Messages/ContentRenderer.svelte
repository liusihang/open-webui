<script>
	import { onDestroy, onMount, tick } from 'svelte';

	import Markdown from './Markdown.svelte';
	import {
		createStreamingTextState,
		drainStreamingTextState,
		getStreamingTextChunkSize,
		syncStreamingTextState
	} from '../streaming';
	import {
		artifactCode,
		chatId,
		mobile,
		settings,
		showArtifacts,
		showControls,
		showEmbeds
	} from '$lib/stores';
	import FloatingButtons from '../ContentRenderer/FloatingButtons.svelte';
	import { createMessagesList } from '$lib/utils';

	export let id;
	export let content;

	export let history;
	export let messageId;

	export let selectedModels = [];

	export let done = true;
	export let model = null;
	export let sources = null;

	export let save = false;
	export let preview = false;
	export let floatingButtons = true;

	export let editCodeBlock = true;
	export let topPadding = false;

	export let onSave = () => {};
	export let onSourceClick = () => {};
	export let onTaskClick = () => {};
	export let onAddMessages = () => {};

	let contentContainerElement;
	let floatingButtonsElement;
	let renderedContent = content ?? '';
	let streamingTextState = createStreamingTextState(renderedContent);
	let streamingTimer = null;

	let sourceIds = [];
	$: getSourceIds(sources);

	const STREAMING_TICK_MS = 40;

	const clearStreamingTimer = () => {
		if (streamingTimer) {
			clearTimeout(streamingTimer);
			streamingTimer = null;
		}
	};

	const flushRenderedContent = (nextContent = '') => {
		clearStreamingTimer();
		streamingTextState = createStreamingTextState(nextContent);
		renderedContent = nextContent;
	};

	const scheduleStreamingDrain = () => {
		if (streamingTimer || done || !streamingTextState.queue) {
			return;
		}

		streamingTimer = setTimeout(() => {
			streamingTimer = null;

			// If HTML blocks appeared in the queue since last drain, flush immediately
			if (/<details[\s>]/.test(streamingTextState.queue)) {
				flushRenderedContent(streamingTextState.target);
				return;
			}

			streamingTextState = drainStreamingTextState(
				streamingTextState,
				getStreamingTextChunkSize(streamingTextState.queue.length)
			);
			renderedContent = streamingTextState.rendered;

			if (!done && streamingTextState.queue) {
				scheduleStreamingDrain();
			}
		}, STREAMING_TICK_MS);
	};

	$: {
		const nextContent = content ?? '';

		if (done) {
			flushRenderedContent(nextContent);
		} else {
			streamingTextState = syncStreamingTextState(streamingTextState, nextContent);

			// If the queue contains HTML blocks (e.g. <details> for tool calls),
			// flush immediately to avoid partial-HTML artifacts during
			// character-by-character streaming that cause the page to flash.
			if (streamingTextState.queue && /<details[\s>]/.test(streamingTextState.queue)) {
				flushRenderedContent(nextContent);
			} else {
				if (!renderedContent && streamingTextState.queue) {
					streamingTextState = drainStreamingTextState(
						streamingTextState,
						Math.min(6, getStreamingTextChunkSize(streamingTextState.queue.length))
					);
					renderedContent = streamingTextState.rendered;
				}

				if (streamingTextState.queue) {
					scheduleStreamingDrain();
				} else {
					clearStreamingTimer();
					renderedContent = streamingTextState.rendered;
				}
			}
		}
	}

	const getSourceIds = (sources) => {
		const result = [];
		for (const source of sources ?? []) {
			for (let index = 0; index < (source.document ?? []).length; index++) {
				if (model?.info?.meta?.capabilities?.citations == false) {
					result.push('N/A');
					continue;
				}
				const metadata = source.metadata?.[index];
				const id = metadata?.source ?? 'N/A';
				if (metadata?.name) {
					result.push(metadata.name);
				} else if (id.startsWith('http://') || id.startsWith('https://')) {
					result.push(id);
				} else {
					result.push(source?.source?.name ?? id);
				}
			}
		}
		sourceIds = [...new Set(result)];
	};

	const updateButtonPosition = (event) => {
		const buttonsContainerElement = document.getElementById(`floating-buttons-${id}`);
		if (
			!contentContainerElement?.contains(event.target) &&
			!buttonsContainerElement?.contains(event.target)
		) {
			closeFloatingButtons();
			return;
		}

		setTimeout(async () => {
			await tick();

			if (!contentContainerElement?.contains(event.target)) return;

			let selection = window.getSelection();

			if (selection.toString().trim().length > 0) {
				const range = selection.getRangeAt(0);
				const rect = range.getBoundingClientRect();

				const parentRect = contentContainerElement.getBoundingClientRect();

				// Adjust based on parent rect
				const top = rect.bottom - parentRect.top;
				const left = rect.left - parentRect.left;

				if (buttonsContainerElement) {
					buttonsContainerElement.style.display = 'block';

					// Calculate space available on the right
					const spaceOnRight = parentRect.width - left;
					let halfScreenWidth = $mobile ? window.innerWidth / 2 : window.innerWidth / 3;

					if (spaceOnRight < halfScreenWidth) {
						const right = parentRect.right - rect.right;
						buttonsContainerElement.style.right = `${right}px`;
						buttonsContainerElement.style.left = 'auto'; // Reset left
					} else {
						// Enough space, position using 'left'
						buttonsContainerElement.style.left = `${left}px`;
						buttonsContainerElement.style.right = 'auto'; // Reset right
					}
					buttonsContainerElement.style.top = `${top + 5}px`; // +5 to add some spacing
				}
			} else {
				closeFloatingButtons();
			}
		}, 0);
	};

	const closeFloatingButtons = () => {
		const buttonsContainerElement = document.getElementById(`floating-buttons-${id}`);
		if (buttonsContainerElement) {
			buttonsContainerElement.style.display = 'none';
		}

		if (floatingButtonsElement) {
			// check if closeHandler is defined

			if (typeof floatingButtonsElement?.closeHandler === 'function') {
				// call the closeHandler function
				floatingButtonsElement?.closeHandler();
			}
		}
	};

	const keydownHandler = (e) => {
		if (e.key === 'Escape') {
			closeFloatingButtons();
		}
	};

	onMount(() => {
		if (floatingButtons) {
			contentContainerElement?.addEventListener('mouseup', updateButtonPosition);
			document.addEventListener('mouseup', updateButtonPosition);
			document.addEventListener('keydown', keydownHandler);
		}
	});

	onDestroy(() => {
		clearStreamingTimer();

		if (floatingButtons) {
			contentContainerElement?.removeEventListener('mouseup', updateButtonPosition);
			document.removeEventListener('mouseup', updateButtonPosition);
			document.removeEventListener('keydown', keydownHandler);
		}
	});
</script>

<div bind:this={contentContainerElement}>
	<Markdown
		{id}
		content={renderedContent}
		{model}
		{save}
		{preview}
		{done}
		{editCodeBlock}
		{topPadding}
		{sourceIds}
		{onSourceClick}
		{onTaskClick}
		{onSave}
		onUpdate={async (token) => {
			const { lang, text: code } = token;

			if (
				($settings?.detectArtifacts ?? true) &&
				(['html', 'svg'].includes(lang) || (lang === 'xml' && code.includes('svg'))) &&
				!$mobile &&
				$chatId
			) {
				await tick();
				showArtifacts.set(true);
				showControls.set(true);
			}
		}}
		onPreview={async (value) => {
			console.log('Preview', value);
			await artifactCode.set(value);
			await showControls.set(true);
			await showArtifacts.set(true);
			await showEmbeds.set(false);
		}}
	/>
</div>

{#if floatingButtons}
	<FloatingButtons
		bind:this={floatingButtonsElement}
		{id}
		{messageId}
		actions={$settings?.floatingActionButtons ?? []}
		model={(selectedModels ?? []).includes(model?.id)
			? model?.id
			: (selectedModels ?? []).length > 0
				? selectedModels.at(0)
				: (model?.id ?? null)}
		messages={createMessagesList(history, messageId)}
		onAdd={({ modelId, parentId, messages }) => {
			console.log(modelId, parentId, messages);
			onAddMessages({ modelId, parentId, messages });
			closeFloatingButtons();
		}}
	/>
{/if}

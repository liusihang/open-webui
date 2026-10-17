<script lang="ts">
	import { getContext, untrack } from 'svelte';
	import type { Readable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import { marked } from 'marked';
	import DOMPurify from 'dompurify';
	import { INTELLIGENT_UI_BLOCK, type IntelligentUIBlockContext } from './context';
	import { mergeWritingStream, type WritingBlockProps } from '$lib/utils/intelligent-ui/blocks';
	let { view }: { view: WritingBlockProps & { __key?: string } } = $props();
	const i18n = getContext<Readable<I18n>>('i18n');
	const context = getContext<IntelligentUIBlockContext>(INTELLIGENT_UI_BLOCK);
	let stateKey = $derived(`writing:${view.__key}`);
	let draft = $state(untrack(() => String(context.read(`writing:${view.__key}`, view.content))));
	let previous = untrack(() => view.content);
	let previousKey = untrack(() => view.__key);
	let editing = $state(false);
	let expanded = $state(false);
	let conflict = $state(false);
	let copyState = $state<'idle' | 'copied' | 'error'>('idle');
	$effect(() => {
		const incoming = view.content;
		const key = view.__key;
		untrack(() => {
			if (key !== previousKey) {
				draft = String(context.read(stateKey, incoming));
				conflict = false;
				editing = false;
			} else {
				const merged = mergeWritingStream(previous, incoming, draft);
				draft = merged.text;
				conflict = conflict || merged.conflict;
			}
			previous = incoming;
			previousKey = key;
		});
	});
	// Keep local drafts through tab/conditional unmounts; the host gates durable writes until done.
	$effect(() => {
		context.isDone();
		if (draft !== view.content) {
			const key = stateKey,
				text = draft;
			untrack(() => context.write(key, text));
		}
	});
	let rendered = $derived(
		view.format === 'markdown' && typeof DOMPurify.sanitize === 'function'
			? DOMPurify.sanitize(marked.parse(draft, { async: false }) as string, {
					ALLOWED_TAGS: [
						'p',
						'br',
						'strong',
						'em',
						'del',
						'h1',
						'h2',
						'h3',
						'h4',
						'h5',
						'h6',
						'ul',
						'ol',
						'li',
						'blockquote',
						'pre',
						'code',
						'hr',
						'table',
						'thead',
						'tbody',
						'tr',
						'th',
						'td'
					],
					ALLOWED_ATTR: []
				})
			: ''
	);
	async function copy() {
		try {
			await navigator.clipboard.writeText(draft);
			copyState = 'copied';
		} catch {
			copyState = 'error';
		}
	}
</script>

<section
	class="overflow-hidden rounded-xl border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900"
	data-writing-block={view.__key}
>
	<header
		class="flex flex-wrap items-center justify-between gap-2 border-b border-gray-200 px-4 py-3 dark:border-gray-700"
	>
		<h3 class="text-sm font-semibold">{view.title}</h3>
		<div class="flex items-center gap-2 text-xs">
			<button
				type="button"
				class="rounded px-2 py-1 hover:bg-gray-100 dark:hover:bg-gray-800"
				aria-pressed={editing}
				onclick={() => {
					editing = !editing;
				}}>{editing ? $i18n.t('Done editing') : $i18n.t('Edit')}</button
			>
			<button
				type="button"
				class="rounded px-2 py-1 hover:bg-gray-100 dark:hover:bg-gray-800"
				onclick={copy}>{$i18n.t('Copy')}</button
			>
			<button
				type="button"
				class="rounded px-2 py-1 hover:bg-gray-100 dark:hover:bg-gray-800"
				aria-expanded={expanded}
				onclick={() => {
					expanded = !expanded;
				}}>{expanded ? $i18n.t('Collapse') : $i18n.t('Expand')}</button
			>
		</div>
	</header>
	{#if conflict}<div
			class="flex flex-wrap items-center gap-2 bg-amber-50 px-4 py-2 text-xs text-amber-900 dark:bg-amber-950 dark:text-amber-100"
			role="status"
		>
			<p>{$i18n.t('The generated text changed. Your edits are preserved.')}</p>
			<button
				type="button"
				class="underline"
				onclick={() => {
					draft = view.content;
					conflict = false;
					context.write(stateKey, draft);
				}}>{$i18n.t('Use generated text')}</button
			>
		</div>{/if}
	<div class:writing-expanded={expanded} class="writing-content overflow-auto p-4">
		{#if editing}
			<textarea
				aria-label={$i18n.t('Edit {{title}}', { title: view.title })}
				bind:value={draft}
				maxlength={16384}
				class="min-h-64 w-full resize-y rounded-lg border border-gray-200 bg-transparent p-3 text-sm leading-6 dark:border-gray-700"
				oninput={(event) => {
					copyState = 'idle';
					context.write(stateKey, event.currentTarget.value);
				}}
			></textarea>
			<p class="mt-2 text-xs text-gray-500">
				{$i18n.t('Edits are saved with this conversation when available.')}
			</p>
		{:else if view.format === 'markdown' && rendered}
			<div class="prose prose-sm max-w-none break-words dark:prose-invert">{@html rendered}</div>
		{:else}
			<div class="whitespace-pre-wrap break-words text-sm leading-7">{draft}</div>
		{/if}
	</div>
	{#if copyState !== 'idle'}<p role="status" class="px-4 pb-3 text-xs text-gray-500">
			{copyState === 'copied'
				? $i18n.t('Copied')
				: $i18n.t('Copy failed. Select and copy the text manually.')}
		</p>{/if}
</section>

<style>
	.writing-content {
		max-height: 24rem;
	}
	.writing-expanded {
		max-height: 75vh;
	}
</style>

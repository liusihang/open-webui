<script lang="ts">
	import { getContext, untrack } from 'svelte';
	import type { Readable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import type { AppBlockProps } from '$lib/utils/intelligent-ui/blocks';
	import {
		acceptAppMessage,
		buildAppDocument,
		newAppToken
	} from '$lib/utils/intelligent-ui/app-sandbox';
	import { INTELLIGENT_UI_BLOCK, type IntelligentUIBlockContext } from './context';
	let { view }: { view: AppBlockProps & { __key?: string } } = $props();
	const context = getContext<IntelligentUIBlockContext & { isDone?: () => boolean }>(
		INTELLIGENT_UI_BLOCK
	);
	const i18n = getContext<Readable<I18n>>('i18n');
	let frame = $state<HTMLIFrameElement>();
	let documentSource = $state('');
	let token = $state('');
	let failed = $state(false);
	let ready = $state(false);
	let finished = $derived(context.isDone?.() === true);
	let keys = $derived(view.stateKeys ?? []);
	let shared = $derived.by(() => Object.fromEntries(keys.map((key) => [key, context.shared(key)])));
	let fingerprint = $derived(JSON.stringify([view.html, view.css, view.js, view.stateKeys]));
	function sendState() {
		frame?.contentWindow?.postMessage(
			{ channel: 'intelligent-ui-app', token, type: 'state', state: shared },
			'*'
		);
	}
	$effect(() => {
		fingerprint;
		if (!finished) {
			documentSource = '';
			ready = false;
			return;
		}
		token = newAppToken();
		ready = false;
		failed = false;
		documentSource = untrack(() => buildAppDocument(view, token));
	});
	$effect(() => {
		if (ready) sendState();
	});
	function receive(event: MessageEvent) {
		if (
			!finished ||
			!frame?.contentWindow ||
			event.source !== frame.contentWindow ||
			event.origin !== 'null'
		)
			return;
		if (
			event.data?.channel === 'intelligent-ui-app' &&
			event.data?.token === token &&
			event.data?.type === 'ready'
		) {
			ready = true;
			sendState();
			return;
		}
		const write = acceptAppMessage(event, frame.contentWindow, token, keys);
		if (write) context.writeShared(write.key, write.value);
	}
</script>

<svelte:window onmessage={receive} />
<section
	class="overflow-hidden rounded-xl border border-gray-200 dark:border-gray-700"
	data-app-block={view.__key}
>
	<header
		class="flex items-center justify-between gap-2 border-b border-gray-200 px-4 py-3 dark:border-gray-700"
	>
		<h3 class="text-sm font-semibold">{view.title}</h3>
		<span class="text-xs text-gray-500">{$i18n.t('Isolated app')}</span>
	</header>
	{#if !finished}
		<p role="status" class="p-4 text-sm text-gray-500">
			{$i18n.t('The app will run when the response is complete.')}
		</p>
	{:else if failed}
		<p role="status" class="p-4 text-sm text-gray-500">{$i18n.t('This app could not run.')}</p>
	{:else if documentSource}
		<iframe
			bind:this={frame}
			title={view.title}
			sandbox="allow-scripts"
			referrerpolicy="no-referrer"
			srcdoc={documentSource}
			style:height={`${view.height ?? 360}px`}
			class="block w-full border-0 bg-white"
			onerror={() => {
				failed = true;
			}}
		></iframe>
	{/if}
</section>

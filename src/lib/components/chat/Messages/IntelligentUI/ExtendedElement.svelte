<script lang="ts">
	import { getContext, tick, type Snippet } from 'svelte';
	import katex from 'katex';
	import type { Readable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	const i18n = getContext<Readable<I18n>>('i18n');
	import 'katex/dist/katex.min.css';
	import { boundText, runActions, sanitizeSvg } from '$lib/utils/intelligent-ui/extended';
	import {
		INTELLIGENT_UI_BLOCK,
		type IntelligentUIBlockContext,
		type InteractionValue
	} from './context';
	import SafeImage from './SafeImage.svelte';
	let {
		element,
		children
	}: { element: { type: string; props: any; children?: string[] }; children?: Snippet } = $props();
	const context = getContext<IntelligentUIBlockContext>(INTELLIGENT_UI_BLOCK);
	const instanceId = $props.id();
	let p = $derived(element.props);
	let value = $derived(p.stateKey ? (context.shared(p.stateKey) ?? p.value) : undefined);
	const write = (value: InteractionValue) => context.writeShared(p.stateKey, value);
	let actionStatus = $state('');
	async function activate() {
		actionStatus = '';
		runActions(p.actions, context.shared, context.writeShared);
		const keys = [
			...new Set<string>(
				p.actions.filter((a: any) => a.type === 'copy').flatMap((a: any) => a.keys)
			)
		];
		if (keys.length)
			try {
				await navigator.clipboard.writeText(
					JSON.stringify(Object.fromEntries(keys.map((key) => [key, context.shared(key)])), null, 2)
				);
				actionStatus = 'Copied';
			} catch {
				actionStatus = 'Could not copy. Clipboard access is unavailable.';
			}
	}
	let summary = $derived(
		element.type === 'Text' ? boundText(p.text, p.bindings, context.shared) : ''
	);
	let math = $derived.by(() => {
		if (element.type !== 'Math') return '';
		try {
			return katex.renderToString(p.expression, {
				displayMode: p.display,
				throwOnError: true,
				trust: false,
				strict: 'error',
				maxExpand: 100,
				maxSize: 20,
				output: 'htmlAndMathml'
			});
		} catch {
			return '';
		}
	});
	let svg = $derived.by(() => {
		if (element.type !== 'SVG') return '';
		try {
			return sanitizeSvg(p.svg);
		} catch {
			return '';
		}
	});
	const icons: Record<string, string> = {
		info: 'ⓘ',
		check: '✓',
		warning: '⚠',
		star: '★',
		'arrow-right': '→',
		heart: '♥'
	};
	let open = $state(false);
	let trigger = $state<HTMLButtonElement>();
	let panel = $state<HTMLDivElement>();
	let popoverRoot = $state<HTMLDivElement>();
	async function toggle() {
		open = !open;
		if (open) {
			await tick();
			panel?.focus();
		}
	}
	function close(restore = false) {
		open = false;
		if (restore) trigger?.focus();
	}
	function outside(event: PointerEvent) {
		if (open && (!(event.target instanceof Node) || !popoverRoot?.contains(event.target))) close();
	}
	function popoverKey(event: KeyboardEvent) {
		if (!open) return;
		if (event.key === 'Escape') {
			event.preventDefault();
			event.stopPropagation();
			close(true);
		}
		if (event.key === 'Tab') {
			const items = Array.from(
				panel?.querySelectorAll<HTMLElement>(
					'button, input, select, textarea, a[href], [tabindex="0"]'
				) ?? []
			).filter((el) => !el.hasAttribute('disabled'));
			if (!items.length) {
				event.preventDefault();
				panel?.focus();
				return;
			}
			const first = items[0],
				last = items.at(-1)!;
			if (
				event.shiftKey &&
				(document.activeElement === first || document.activeElement === panel)
			) {
				event.preventDefault();
				last.focus();
			} else if (
				!event.shiftKey &&
				(document.activeElement === last || document.activeElement === panel)
			) {
				event.preventDefault();
				first.focus();
			}
		}
	}
	const slide = (index: number) =>
		context.write(`carousel:${p.__key}`, Math.max(0, Math.min((p.__count ?? 1) - 1, index)));
	function carouselKey(event: KeyboardEvent) {
		if (event.target !== event.currentTarget) return;
		let next = p.__index ?? 0;
		if (event.key === 'ArrowRight') next++;
		else if (event.key === 'ArrowLeft') next--;
		else if (event.key === 'Home') next = 0;
		else if (event.key === 'End') next = (p.__count ?? 1) - 1;
		else return;
		event.preventDefault();
		slide(next);
	}
	const inputClass =
		'w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-transparent px-3 py-2 focus:ring-2 focus:ring-blue-500';
</script>

<svelte:window onpointerdown={outside} />
{#if element.type === 'Card'}
	<section class="space-y-3 rounded-xl border border-gray-200 p-4 dark:border-gray-700">
		{#if p.title}<h4 class="font-semibold">{p.title}</h4>{/if}{#if p.description}<p
				class="text-sm text-gray-600 dark:text-gray-400"
			>
				{p.description}
			</p>{/if}
		{@render children?.()}
	</section>
{:else if element.type === 'Carousel'}
	<section
		class="space-y-3 rounded-xl border border-gray-200 p-4 dark:border-gray-700"
		aria-label={p.title ?? $i18n.t('Carousel')}
		aria-roledescription="carousel"
	>
		{#if p.title}<h4 class="font-semibold">{p.title}</h4>{/if}
		<!-- A focusable slide viewport provides keyboard navigation without intercepting child controls. -->
		<!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
		<div
			role="group"
			tabindex="0"
			aria-label={$i18n.t('Carousel slides. Use left and right arrow keys to navigate.')}
			onkeydown={carouselKey}
		>
			{@render children?.()}
		</div>
		<div class="flex items-center justify-between gap-3">
			<button
				type="button"
				class="rounded-lg border px-3 py-1 disabled:opacity-40"
				aria-label={$i18n.t('Previous slide')}
				disabled={(p.__index ?? 0) <= 0}
				onclick={() => slide((p.__index ?? 0) - 1)}>←</button
			>
			<span class="text-sm" aria-live="polite">{(p.__index ?? 0) + 1} / {p.__count ?? 1}</span>
			<button
				type="button"
				class="rounded-lg border px-3 py-1 disabled:opacity-40"
				aria-label={$i18n.t('Next slide')}
				disabled={(p.__index ?? 0) >= (p.__count ?? 1) - 1}
				onclick={() => slide((p.__index ?? 0) + 1)}>→</button
			>
		</div>
	</section>
{:else if element.type === 'Flow'}
	<section aria-label={p.title ?? $i18n.t('Steps')}>
		{#if p.title}<h4 class="mb-3 font-semibold">{p.title}</h4>{/if}
		<ol class="space-y-3">
			{#each p.steps as step, i}<li class="flex gap-3">
					<span
						class="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-gray-100 text-sm dark:bg-gray-800"
						>{i + 1}</span
					>
					<div>
						<p class="font-medium">{step.title}</p>
						{#if step.description}<p class="text-sm text-gray-600 dark:text-gray-400">
								{step.description}
							</p>{/if}
					</div>
				</li>{/each}
		</ol>
	</section>
{:else if element.type === 'Popover'}
	<div bind:this={popoverRoot} class="relative" role="group">
		<button
			type="button"
			bind:this={trigger}
			class="rounded-lg border px-3 py-2"
			aria-haspopup="dialog"
			aria-expanded={open}
			aria-controls={`${instanceId}-popover`}
			onclick={toggle}>{p.label}</button
		>
		{#if open}<div
				id={`${instanceId}-popover`}
				bind:this={panel}
				onkeydown={popoverKey}
				role="dialog"
				aria-label={p.title ?? p.label}
				tabindex="-1"
				class="relative z-10 mt-2 max-w-full space-y-3 rounded-xl border border-gray-300 bg-white p-4 shadow-lg dark:border-gray-600 dark:bg-gray-900"
			>
				<div class="flex items-center justify-between gap-4">
					<h4 class="font-semibold">{p.title ?? p.label}</h4>
					<button
						type="button"
						aria-label={$i18n.t('Close popover')}
						class="rounded p-1"
						onclick={() => close(true)}>✕</button
					>
				</div>
				{@render children?.()}
			</div>{/if}
	</div>
{:else if element.type === 'SVG'}
	<div role="img" aria-label={p.label} class="safe-svg max-h-96 overflow-auto">{@html svg}</div>
{:else if element.type === 'Icon'}
	<span role="img" aria-label={p.label} class="inline-flex text-2xl">{icons[p.name]}</span>
{:else if element.type === 'Math'}
	<div class="overflow-auto py-2">
		{#if math}{@html math}{:else}<span class="text-sm"
				>{$i18n.t('Formula unavailable')}: {p.expression}</span
			>{/if}
	</div>
{:else if element.type === 'Image'}<SafeImage image={p} />
{:else if element.type === 'ImageGallery'}
	<section>
		{#if p.title}<h4 class="mb-3 font-semibold">{p.title}</h4>{/if}
		<div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
			{#each p.images as image}<SafeImage {image} />{/each}
		</div>
	</section>
{:else if element.type === 'Input'}
	<label class="block space-y-1"
		><span class="text-sm font-medium">{p.title}</span><input
			type="text"
			class={inputClass}
			value={String(value ?? '')}
			maxlength={p.maxLength}
			placeholder={p.placeholder}
			oninput={(event) => write(event.currentTarget.value.slice(0, p.maxLength))}
		/></label
	>
{:else if element.type === 'Textarea'}
	<label class="block space-y-1"
		><span class="text-sm font-medium">{p.title}</span><textarea
			class={inputClass}
			rows="4"
			value={String(value ?? '')}
			maxlength={p.maxLength}
			placeholder={p.placeholder}
			oninput={(event) => write(event.currentTarget.value.slice(0, p.maxLength))}
		></textarea></label
	>
{:else if element.type === 'Checkbox'}
	<label class="flex items-center gap-2"
		><input
			type="checkbox"
			checked={value === true}
			onchange={(event) => write(event.currentTarget.checked)}
		/><span>{p.title}</span></label
	>
{:else if element.type === 'RadioGroup' || element.type === 'SegmentedControl'}
	<fieldset class="min-w-0">
		<legend class="mb-2 text-sm font-medium">{p.title}</legend>
		<div class={element.type === 'SegmentedControl' ? 'flex flex-wrap gap-2' : 'space-y-2'}>
			{#each p.options as option}<label
					class={element.type === 'SegmentedControl'
						? 'flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2'
						: 'flex items-center gap-2'}
					><input
						type="radio"
						name={`iui-${instanceId}`}
						value={option.value}
						checked={value === option.value}
						onchange={() => write(option.value)}
					/><span>{option.label}</span></label
				>{/each}
		</div>
	</fieldset>
{:else if element.type === 'DatePicker'}
	<label class="block space-y-1"
		><span class="text-sm font-medium">{p.title}</span><input
			type="date"
			class={inputClass}
			value={String(value ?? '')}
			min={p.min}
			max={p.max}
			onchange={(event) => write(event.currentTarget.value)}
		/></label
	>
{:else if element.type === 'Pressable'}
	<div
		role="button"
		tabindex="0"
		aria-label={p.label}
		class="cursor-pointer space-y-3 rounded-xl border border-gray-300 p-4 hover:bg-gray-50 focus:ring-2 focus:ring-blue-500 dark:border-gray-600 dark:hover:bg-gray-800"
		onclick={activate}
		onkeydown={(event) => {
			if (event.key === 'Enter' || event.key === ' ') {
				event.preventDefault();
				if (!event.repeat) activate();
			}
		}}
	>
		<p class="font-medium">{p.label}</p>
		{@render children?.()}
		{#if actionStatus}<p role="status" class="text-sm">{$i18n.t(actionStatus)}</p>{/if}
	</div>
{:else if element.type === 'Button'}
	<div class="space-y-2">
		<button
			type="button"
			class="rounded-lg bg-gray-900 px-4 py-2 text-white focus:ring-2 focus:ring-blue-500 dark:bg-gray-100 dark:text-gray-900"
			onclick={activate}>{p.label}</button
		>{#if actionStatus}<p role="status" class="text-sm">{$i18n.t(actionStatus)}</p>{/if}
		{@render children?.()}
	</div>
{:else if element.type === 'Text'}<p class="whitespace-pre-wrap break-words">{summary}</p>
{:else if element.type === 'Conditional'}{@render children?.()}
{/if}

<style>
	.safe-svg :global(svg) {
		max-width: 100%;
		max-height: 24rem;
		height: auto;
	}
</style>

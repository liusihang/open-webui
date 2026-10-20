<script lang="ts">
	import { getContext, tick, type Snippet } from 'svelte';
	import katex from 'katex';
	import type { Readable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	const i18n = getContext<Readable<I18n>>('i18n');
	import 'katex/dist/katex.min.css';
	import {
		boundText,
		runActions,
		sanitizeSvg,
		type TextSpan
	} from '$lib/utils/intelligent-ui/extended';
	import { appearanceStyle } from '$lib/utils/intelligent-ui/colors';
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
	let p = $derived({
		...element.props,
		appearance: element.props.__appearance ?? element.props.appearance
	});
	let surfaceStyle = $derived(appearanceStyle(p.appearance, { surface: true }));
	let inkStyle = $derived(p.appearance ? appearanceStyle(p.appearance) : undefined);
	let inkSurface = $derived(
		Boolean(element.props.appearance?.background || element.props.appearance?.border)
	);
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
	let spans = $derived(
		element.type === 'Text' && p.spans
			? p.spans.map((span: TextSpan) => ({
					...span,
					text: boundText(span.text, p.bindings, context.shared)
				}))
			: undefined
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
	const inputClass = 'iui-surface iui-interactive w-full rounded-xl border px-3 py-2.5';
</script>

<svelte:window onpointerdown={outside} />
{#if element.type === 'Card'}
	<section class="iui-surface space-y-3 rounded-2xl border p-5" style={surfaceStyle}>
		{#if p.title}<h4 class="font-semibold">{p.title}</h4>{/if}{#if p.description}<p class="text-sm">
				{p.description}
			</p>{/if}
		{@render children?.()}
	</section>
{:else if element.type === 'Carousel'}
	<section
		class="iui-surface space-y-3 rounded-2xl border p-5"
		style={surfaceStyle}
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
				class="iui-accent iui-interactive rounded-lg border px-3 py-1 disabled:opacity-40"
				aria-label={$i18n.t('Previous slide')}
				disabled={(p.__index ?? 0) <= 0}
				onclick={() => slide((p.__index ?? 0) - 1)}>←</button
			>
			<span class="text-sm" aria-live="polite">{(p.__index ?? 0) + 1} / {p.__count ?? 1}</span>
			<button
				type="button"
				class="iui-accent iui-interactive rounded-lg border px-3 py-1 disabled:opacity-40"
				aria-label={$i18n.t('Next slide')}
				disabled={(p.__index ?? 0) >= (p.__count ?? 1) - 1}
				onclick={() => slide((p.__index ?? 0) + 1)}>→</button
			>
		</div>
	</section>
{:else if element.type === 'Flow'}
	<section
		class="iui-surface rounded-2xl border p-5"
		style={surfaceStyle}
		aria-label={p.title ?? $i18n.t('Steps')}
	>
		{#if p.title}<h4 class="mb-3 font-semibold">{p.title}</h4>{/if}
		<ol class="space-y-3">
			{#each p.steps as step, i}<li
					class="iui-surface flex gap-3 rounded-xl border p-3"
					style={appearanceStyle({ ...p.appearance, ...step.appearance }, { surface: true })}
				>
					<span
						class="iui-accent flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-sm font-semibold"
						>{i + 1}</span
					>
					<div>
						<p class="font-medium">{step.title}</p>
						{#if step.description}<p class="text-sm">
								{step.description}
							</p>{/if}
					</div>
				</li>{/each}
		</ol>
	</section>
{:else if element.type === 'Popover'}
	<div bind:this={popoverRoot} class="relative" style={surfaceStyle} role="group">
		<button
			type="button"
			bind:this={trigger}
			class="iui-surface iui-interactive rounded-xl border px-3 py-2"
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
				class="iui-surface relative z-10 mt-2 max-w-full space-y-3 rounded-2xl border p-5 shadow-lg"
			>
				<div class="flex items-center justify-between gap-4">
					<h4 class="font-semibold">{p.title ?? p.label}</h4>
					<button
						type="button"
						aria-label={$i18n.t('Close popover')}
						class="iui-interactive rounded-lg p-1"
						onclick={() => close(true)}>✕</button
					>
				</div>
				{@render children?.()}
			</div>{/if}
	</div>
{:else if element.type === 'SVG'}
	<div role="img" aria-label={p.label} class="safe-svg max-h-96 overflow-auto">{@html svg}</div>
{:else if element.type === 'Icon'}
	<span
		role="img"
		aria-label={p.label}
		class="iui-ink inline-flex text-2xl"
		class:iui-surface={inkSurface}
		class:rounded-lg={inkSurface}
		class:px-2={inkSurface}
		class:py-1={inkSurface}
		class:border={Boolean(element.props.appearance?.border)}
		style={inkStyle}>{icons[p.name]}</span
	>
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
	<label class="iui-surface block space-y-2 rounded-2xl border p-4" style={surfaceStyle}
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
	<label class="iui-surface block space-y-2 rounded-2xl border p-4" style={surfaceStyle}
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
	<label
		class="iui-surface flex items-center gap-3 rounded-xl border px-3 py-2.5"
		style={surfaceStyle}
		><input
			type="checkbox"
			class="iui-accent iui-interactive h-4 w-4 rounded"
			checked={value === true}
			onchange={(event) => write(event.currentTarget.checked)}
		/><span>{p.title}</span></label
	>
{:else if element.type === 'RadioGroup' || element.type === 'SegmentedControl'}
	<fieldset class="iui-surface min-w-0 rounded-2xl border p-4" style={surfaceStyle}>
		<legend class="mb-2 text-sm font-medium">{p.title}</legend>
		<div class={element.type === 'SegmentedControl' ? 'flex flex-wrap gap-2' : 'space-y-2'}>
			{#each p.options as option}<label
					class={element.type === 'SegmentedControl'
						? `iui-option ${value === option.value ? 'is-selected' : ''} flex cursor-pointer items-center gap-2 rounded-xl border px-3 py-2`
						: 'flex cursor-pointer items-center gap-2 rounded-lg px-1 py-1'}
					><input
						type="radio"
						class="iui-accent iui-interactive h-4 w-4"
						name={`iui-${instanceId}`}
						value={option.value}
						checked={value === option.value}
						onchange={() => write(option.value)}
					/><span>{option.label}</span></label
				>{/each}
		</div>
	</fieldset>
{:else if element.type === 'DatePicker'}
	<label class="iui-surface block space-y-2 rounded-2xl border p-4" style={surfaceStyle}
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
		class="iui-surface iui-interactive cursor-pointer space-y-3 rounded-2xl border p-5"
		style={surfaceStyle}
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
			class="iui-surface iui-strong iui-interactive rounded-xl border px-4 py-2.5 font-semibold"
			style={appearanceStyle(p.appearance, { surface: true, strong: true })}
			onclick={activate}>{p.label}</button
		>{#if actionStatus}<p role="status" class="text-sm">{$i18n.t(actionStatus)}</p>{/if}
		{@render children?.()}
	</div>
{:else if element.type === 'Text'}
	<p
		class="iui-ink whitespace-pre-wrap break-words"
		class:iui-surface={inkSurface}
		class:rounded-xl={inkSurface}
		class:p-3={inkSurface}
		class:border={Boolean(element.props.appearance?.border)}
		style={inkStyle}
	>
		{#if spans}{#each spans as span}<span
					class:iui-ink={span.color !== undefined}
					class:font-bold={span.bold === true}
					style={span.color === undefined
						? undefined
						: appearanceStyle({ ...p.appearance, foreground: span.color })}>{span.text}</span
				>{/each}{:else}{summary}{/if}
	</p>
{:else if element.type === 'Conditional'}{@render children?.()}
{/if}

<style>
	.iui-interactive {
		transition:
			filter 120ms ease,
			box-shadow 120ms ease;
	}
	.iui-interactive:focus-visible,
	.iui-option:focus-within {
		outline: 2px solid var(--iui-accent);
		outline-offset: 3px;
	}
	.iui-interactive:hover:not(:disabled) {
		filter: brightness(0.97);
	}
	.iui-option {
		border-color: var(--iui-border);
	}
	.iui-option.is-selected {
		border-color: var(--iui-accent);
		box-shadow: inset 0 0 0 1px var(--iui-accent);
		font-weight: 600;
	}
	@media (prefers-reduced-motion: reduce) {
		.iui-interactive {
			transition: none;
		}
	}
	.safe-svg :global(svg) {
		max-width: 100%;
		max-height: 24rem;
		height: auto;
	}
</style>

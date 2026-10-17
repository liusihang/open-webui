<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { INTELLIGENT_UI_BLOCK, type IntelligentUIBlockContext } from './context';
	let {
		view
	}: { view: { __key: string; __scope?: string; title?: string; language: string; code: string } } =
		$props();
	const context = getContext<IntelligentUIBlockContext>(INTELLIGENT_UI_BLOCK);
	let Component = $state<any>(null);
	let failed = $state(false);
	// Reuse the normal message editor, syntax highlighter and hardened Mermaid renderer.
	// Code execution and external preview are deliberately unavailable in this catalog.
	onMount(() => {
		let active = true;
		import('../CodeBlock.svelte')
			.then((module) => {
				if (active) Component = module.default;
			})
			.catch(() => {
				if (active) failed = true;
			});
		return () => {
			active = false;
		};
	});
</script>

<section class="min-w-0 space-y-2">
	{#if view.title}<h3 class="text-sm font-semibold">{view.title}</h3>{/if}
	{#if Component}
		<Component
			id={`intelligent-code-${view.__scope}-${view.__key}`}
			lang={view.language}
			code={view.code}
			token={{ text: view.code, raw: context.isDone() ? '```' : '' }}
			run={false}
			preview={false}
			edit={true}
		/>
	{:else}
		<pre class="overflow-auto rounded-xl bg-gray-100 p-4 text-sm dark:bg-gray-900"><code
				>{view.code}</code
			></pre>
		{#if failed}<p role="status" class="text-xs text-gray-500">
				Code editor unavailable; source is shown above.
			</p>{/if}
	{/if}
</section>

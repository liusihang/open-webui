<script lang="ts">
	import { setContext, untrack } from 'svelte';
	import StructuredOutputRenderer from './StructuredOutputRenderer.svelte';
	import { createTestI18n } from './IntelligentUI/test-i18n';
	import type { OutputItem } from './structuredOutput';

	let {
		initialOutput = [],
		initialDone = false,
		renderMarkdown = true,
		preview = false,
		compactPreview = false,
		locale = 'en-US'
	}: {
		initialOutput?: OutputItem[];
		initialDone?: boolean;
		renderMarkdown?: boolean;
		preview?: boolean;
		compactPreview?: boolean;
		locale?: string;
	} = $props();
	let output = $state(untrack(() => initialOutput));
	let done = $state(untrack(() => initialDone));
	setContext('i18n', createTestI18n(untrack(() => locale)));
	export function update(next: OutputItem[], finished: boolean) {
		output = next;
		done = finished;
	}
</script>

<StructuredOutputRenderer
	id="commentary-test"
	{output}
	{done}
	{renderMarkdown}
	{preview}
	{compactPreview}
/>

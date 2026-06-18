<script lang="ts">
	import type { AgentRunDetailSection } from './renderModel';

	export let section: AgentRunDetailSection;

	const sectionTone = (kind: AgentRunDetailSection['kind']) => {
		if (kind === 'error') {
			return 'border-red-200 bg-red-50/70 text-red-900 dark:border-red-900/60 dark:bg-red-950/20 dark:text-red-100';
		}
		if (kind === 'input') {
			return 'border-blue-200 bg-blue-50/50 text-blue-950 dark:border-blue-900/50 dark:bg-blue-950/20 dark:text-blue-100';
		}
		if (kind === 'output') {
			return 'border-emerald-200 bg-emerald-50/50 text-emerald-950 dark:border-emerald-900/50 dark:bg-emerald-950/20 dark:text-emerald-100';
		}

		return 'border-gray-200 bg-gray-50/80 text-gray-800 dark:border-gray-800 dark:bg-black/20 dark:text-gray-100';
	};

	const detailsText = (value: unknown) =>
		typeof value === 'string' ? value : JSON.stringify(value, null, 2);
</script>

<section class="space-y-1.5">
	<div class="text-[11px] font-medium text-gray-500 dark:text-gray-400">{section.title}</div>
	<pre
		class="max-h-64 overflow-auto whitespace-pre-wrap rounded-md border p-2 text-xs leading-relaxed {sectionTone(
			section.kind
		)}">{detailsText(section.value)}</pre>
</section>
